"""
ui/live/live_tab.py
Live Control 탭 — QMainWindow 기반 도킹 레이아웃.

Dock 구성:
  Left   : Camera Control  (위) + Motors (아래)
  Center : ImageViewer  (중앙 위젯)
  Bottom : Profile Plot (왼) + Histogram (오른)
  Right  : System Log

Toolbar: 각 Dock 토글 + FIT + FREEZE + SAVE
"""

from __future__ import annotations

import csv
import os
import threading
from datetime import datetime
from typing import Optional

import numpy as np
from core.motor.acs_stage import AcsStageController
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QToolBar,
    QScrollArea, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QSizePolicy,
    QApplication, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, QTimer, QSize, QSettings, QThread, QObject, pyqtSignal, QEvent
from PyQt6.QtGui import QAction

from core.camera.base import BaseCamera
from core.camera.hikvision import HikvisionCamera, list_devices as hik_devices
from core.camera.picamp import PicamCamera, list_devices as picam_devices
from core.camera.simulated import SimulatedCamera, list_devices as sim_devices
from core.image_processor import ImageProcessor
from core.spe_writer import save_spe   # #14 라이브 SPE 저장
from ui.image_viewer import ImageViewer
from ui.plot_panel import PlotPanel, HistogramPanel
from ui.live.camera_panel import CameraControlPanel
from ui.live.motor_panel import MotorPanel
from ui.live.kimm_z_panel import KIMMZPanel
from ui.live.acs_stage_panel import AcsStagePanel
from theme.styles import Fonts, Sizes, C_ACCENT, C_TEXT_DEAD, BTN_PRIMARY, TEXTEDIT_LOG
from ui.widgets.collapsible_section import CollapsibleSection

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

_FC         = Fonts.MONO
_FS_TOOLBAR = Sizes.LOG
_FS_LOG     = Sizes.LOG
_FS_HDR     = Sizes.SMALL
_FS_SMALL   = Sizes.SMALL


# ── 카메라 연결 백그라운드 워커 ──────────────────────────────────────────────

class _SnapWorker(QObject):
    """snap() 1장 촬영을 백그라운드에서 실행 — UI 멈춤 방지."""
    success = pyqtSignal(object)   # np.ndarray
    error   = pyqtSignal(str)

    def __init__(self, camera: BaseCamera):
        super().__init__()
        self._camera = camera

    def run(self):
        try:
            frame = self._camera.snap()
            self.success.emit(np.asarray(frame))
        except Exception as e:
            self.error.emit(str(e))


class _DisconnectWorker(QObject):
    """stop_live() + disconnect()를 백그라운드에서 순차 실행."""
    done = pyqtSignal()

    def __init__(self, camera: BaseCamera):
        super().__init__()
        self._camera = camera

    def run(self):
        try:
            self._camera.stop_live()
        except Exception:
            pass
        try:
            self._camera.disconnect()
        except Exception:
            pass
        self.done.emit()


class _ConnectWorker(QObject):
    """카메라 connect()를 백그라운드에서 실행 — UI 멈춤 방지."""
    success = pyqtSignal(object)   # BaseCamera
    error   = pyqtSignal(str)

    def __init__(self, camera: BaseCamera):
        super().__init__()
        self._camera = camera

    def run(self):
        try:
            self._camera.connect()
            self.success.emit(self._camera)
        except Exception as e:
            self.error.emit(str(e))


# ── 이미지 처리 백그라운드 워커 ─────────────────────────────────────────────────

def _build_rgb(result, cmap: str, show_binary: bool, vmin: int, vmax: int) -> np.ndarray:
    """ProcessedFrame → display RGB. 백그라운드 스레드에서 호출됨.

    colormap 경로: result.raw (원본 dtype, uint16 등) 기준으로 vmin/vmax 적용.
    그래야 Range 슬라이더(raw 좌표계)와 단위계가 일치한다.
    colormap 없는 경로: result.display (uint8) 그대로 사용.
    """
    disp = result.display
    if cmap and cmap != 'off' and not show_binary:
        # raw 원본에 vmin/vmax 적용 — Range 슬라이더 단위계와 일치
        src = result.raw if result.raw.ndim == 2 else disp
        from ui.image_viewer import apply_colormap
        rgba = apply_colormap(src.astype(np.float64), cmap, vmin=vmin, vmax=vmax)
        rgb  = rgba[:, :, :3].copy()
    elif disp.ndim == 2:
        rgb = cv2.cvtColor(disp, cv2.COLOR_GRAY2RGB) if _CV2_OK else \
              np.stack([disp, disp, disp], axis=-1)
    else:
        rgb = disp.copy()

    if result.has_centroid and _CV2_OK:
        ix, iy = int(round(result.centroid_x)), int(round(result.centroid_y))
        cv2.drawMarker(rgb, (ix, iy), (78, 205, 196), cv2.MARKER_CROSS, 40, 2)
        cv2.putText(rgb,
            f"({result.centroid_x:.1f}, {result.centroid_y:.1f})",
            (ix + 12, iy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (233, 69, 96), 1)
    return rgb


class _ProcessWorker(QObject):
    """
    Python daemon thread + QObject 시그널 브리지 구조.

    - QThread를 쓰지 않으므로 'Destroyed while running' 에러 없음.
    - daemon=True → 앱 종료 시 스레드 자동 소멸.
    - submit()은 즉시 반환; 처리 중 새 프레임이 오면 자동 드롭(최신 프레임 유지).
    - result_ready는 메인 스레드 QObject에 정의되어 queued connection으로 GUI에 전달.
    - colormap 적용도 여기서 처리 — 메인 스레드 블로킹 없음.
    """
    result_ready = pyqtSignal(object, object)   # (ProcessedFrame, rgb ndarray) → GUI

    def __init__(self, proc: ImageProcessor, parent=None):
        super().__init__(parent)
        self._proc   = proc
        self._cmap   = 'off'   # GIL로 보호되는 단순 str 대입 — 스레드 안전
        self._latest_raw: Optional[np.ndarray] = None
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        self._stop   = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="ImgProcWorker", daemon=True
        )
        self._vmax = 65535
        self._vmin = 0
        self._thread.start()

    def submit(self, raw: np.ndarray):
        """카메라 스레드에서 호출 — 즉시 반환, 블로킹 없음."""
        with self._lock:
            self._latest_raw = raw
        self._event.set()

    def set_cmap(self, cmap: str):
        """메인 스레드에서 호출 — 다음 프레임부터 반영."""
        self._cmap = cmap

    def _run(self):
        while not self._stop.is_set():
            if not self._event.wait(timeout=0.1):
                continue
            self._event.clear()
            with self._lock:
                raw = self._latest_raw
                self._latest_raw = None
            if raw is not None:
                try:
                    result = self._proc.process(raw)
                    show_binary = getattr(self._proc, 'show_binary', False)
                    rgb = _build_rgb(result, self._cmap, show_binary, self._vmin, self._vmax)
                    self.result_ready.emit(result, rgb)
                except Exception as e:
                    print(f"[ProcessWorker] {e}")

    def stop(self):
        """정지 요청 — daemon thread이므로 join 실패해도 앱 종료에 지장 없음."""
        self._stop.set()
        self._event.set()
        self._thread.join(timeout=1.0)


# ─────────────────────────────────────────────────────────────────────────────

class LiveTab(QMainWindow):
    """
    Live Control 탭.
    QTabWidget 임베드용: setWindowFlags(Widget) + menuBar 숨김.
    """

    status_message      = pyqtSignal(str)
    camera_connected    = pyqtSignal(object)   # BaseCamera — Acquisition 탭 공유
    camera_disconnected = pyqtSignal()
    exposure_applied    = pyqtSignal(float)
    frame_stats_updated = pyqtSignal(float, int, int)  # fps, width, height

    def on_range_changed(self, vmin, vmax):
        # None = auto range (ROI Range 해제 시 등)
        self._proc_worker._vmin = vmin
        self._proc_worker._vmax = vmax
        self.image_viewer._display_vmin = vmin
        self.image_viewer._display_vmax = vmax
        # frozen 여부와 무관하게 last_raw 가 있으면 재처리:
        # - freeze 모드: 고정 프레임 즉시 갱신
        # - snap 후 정지: 카메라 프레임 없어도 갱신
        # - 라이브 스트리밍 중: 다음 프레임이 곧 오지만 한 장 추가 처리로 즉각 반영
        if self._last_raw is not None:
            self._last_display_t = 0.0  # 30fps 캡 우회
            self._proc_worker.submit(self._last_raw)

    def _on_viewer_colormap_changed(self, cmap: str):
        """ImageViewer 컬러맵 변경을 라이브 처리 파이프라인에 즉시 반영한다."""
        self._proc_worker.set_cmap(cmap)
        if self._last_raw is not None:
            self._last_display_t = 0.0  # 스냅/정지 상태에서도 즉시 redraw
            self._proc_worker.submit(self._last_raw)

    def __init__(self, parent=None, acs_ctrl: AcsStageController = None):
        super().__init__(parent)
        self.acs_ctrl = acs_ctrl
        self.setWindowFlags(Qt.WindowType.Widget)
        self.menuBar().setVisible(False)

        # 모든 QDockWidget 타이틀바를 setTitleBarWidget(QWidget())로 숨기므로
        # QDockWidget::title QSS 불필요 — 플로트/클로즈 버튼만 투명화
        self.setStyleSheet("""
            QDockWidget::float-button, QDockWidget::close-button {
                background: transparent; border: none;
            }
        """)

        self._camera: Optional[BaseCamera] = None
        self._proc = ImageProcessor()
        self._last_raw: Optional[np.ndarray] = None
        self._last_display: Optional[np.ndarray] = None
        self._viewer_raw: Optional[np.ndarray] = None   # 뷰어에 실제 표시된 프레임의 raw
        self._last_centroid = (None, None, 0, 0.0, 0.0, 0.0, False, 0.0)
        self._csv_path = "live_capture_log.csv"

        # ── #4 Freeze / #5 Auto-FIT ──
        self._frozen      = False
        self._first_frame = False
        self._last_display_t: float = 0.0   # display rate cap용

        # ── Disconnect / Connect / Snap workers ──
        self._disc_thread: Optional[QThread] = None
        self._disc_worker: Optional[_DisconnectWorker] = None
        self._conn_thread: Optional[QThread] = None
        self._conn_worker: Optional[_ConnectWorker] = None
        self._snap_thread: Optional[QThread] = None
        self._snap_worker: Optional[_SnapWorker] = None
        self._pending_connect_index: Optional[int] = None  # 해제 후 재연결 대기

        # ── 처리 전용 워커 (카메라 스레드 블로킹 방지) ──
        self._proc_worker = _ProcessWorker(self._proc)
        self._proc_worker.result_ready.connect(self._on_processed)

        self._build_ui()
        self._setup_toolbar()
        self._connect_signals()

        self._log("Live Tab Initialized", "sys")

        # #21 독 레이아웃 복원
        _s = QSettings("SpeAnalyze", "LiveTab")
        _state = _s.value("dockState")
        if _state is not None:
            self.restoreState(_state)

        self._centroid_timer = QTimer()
        self._centroid_timer.setInterval(200)
        self._centroid_timer.timeout.connect(self._refresh_centroid_labels)
        self._centroid_timer.start()

    def stop_polling(self):
        """프로그램 종료 시 모든 타이머와 백그라운드 워커를 안전하게 정지."""
        if hasattr(self, "_centroid_timer") and self._centroid_timer.isActive():
            self._centroid_timer.stop()
        
        if hasattr(self, "_proc_worker"):
            self._proc_worker.stop()
            
        # 자식 패널들 정지
        for panel in [self.camera_panel, self.motor_panel, self.kimm_z_panel, self.acs_stage_panel]:
            if hasattr(panel, "stop_polling"):
                try:
                    panel.stop_polling()
                except Exception as e:
                    print(f"Error stopping panel {type(panel).__name__}: {e}")

    # ── 이벤트 필터 (사이드바 더블클릭 → auto-fit) ───────────────────

    def eventFilter(self, obj, event) -> bool:
        if (obj is getattr(self, '_left_scroll_ref', None)
                and event.type() == QEvent.Type.MouseButtonDblClick):
            sidebar = getattr(self, '_sidebar_ref', None)
            if sidebar is not None:
                hint_w = sidebar.sizeHint().width() + 24   # 여유 padding
                hint_w = max(hint_w, self.dock_left.minimumWidth())
                hint_w = min(hint_w, self.dock_left.maximumWidth())
                self.resizeDocks(
                    [self.dock_left], [hint_w], Qt.Orientation.Horizontal
                )
            return True
        return super().eventFilter(obj, event)

    # ── 독 헬퍼 ──────────────────────────────────────────────────────

    def _make_dock_header(self, title: str) -> QWidget:
        """슬림 커스텀 독 헤더 바 (타이틀바 대체)."""
        hdr = QWidget()
        hdr.setFixedHeight(22)
        hdr.setStyleSheet(
            f"background: #0c1428; border-bottom: 1px solid #0f3460;"
        )
        row = QHBoxLayout(hdr)
        row.setContentsMargins(8, 0, 8, 0)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: #3a5878; font-family: '{_FC}'; font-size: {_FS_HDR};"
            " font-weight: bold; letter-spacing: 2px;"
            " background: transparent; border: none;"
        )
        row.addWidget(lbl)
        return hdr

    def _wrap_dock(
        self,
        obj_name: str,
        title: str,
        content: QWidget,
        area: Qt.DockWidgetArea,
    ) -> QDockWidget:
        """커스텀 헤더를 타이틀바 위젯으로 사용하여 드래그 가능한 QDockWidget 반환."""
        dock = QDockWidget(self)
        dock.setObjectName(obj_name)
        
        # 커스텀 헤더를 타이틀바 위젯으로 설정 (마우스 핸들 역할)
        hdr = self._make_dock_header(title)
        dock.setTitleBarWidget(hdr)
        
        # 실제 콘텐츠 위젯
        dock.setWidget(content)
        
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(area, dock)
        return dock

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── 중앙: ImageViewer ─────────────────────────────────────────
        self.image_viewer = ImageViewer()
        self.image_viewer.set_external_render_control(True)
        self.setCentralWidget(self.image_viewer)
        self.image_viewer.range_changed.connect(self.on_range_changed)
        # ── 좌측 사이드바: CollapsibleSections 1개 독 (타이틀바 숨김) ──────
        # LightField 스타일: 고정폭 패널 안에 접기/펼치기 가능한 섹션 스택
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setStyleSheet("QWidget#sidebar { background: #080e1e; }")
        sidebar_v = QVBoxLayout(sidebar)
        sidebar_v.setContentsMargins(6, 6, 6, 6)
        sidebar_v.setSpacing(2)

        # Camera 섹션
        self._sec_cam = CollapsibleSection("📷  CAMERA CONTROL", accent=C_ACCENT)
        self.camera_panel = CameraControlPanel(self._proc)
        self._sec_cam.add_widget(self.camera_panel)
        sidebar_v.addWidget(self._sec_cam)

        # Motors 섹션
        self._sec_motor = CollapsibleSection("⚙  MOTORS", accent="#4a9a7a")
        self.motor_panel = MotorPanel()
        self._sec_motor.add_widget(self.motor_panel)
        sidebar_v.addWidget(self._sec_motor)

        # KIMM Z 섹션 (기본 접힘)
        self._sec_kimm = CollapsibleSection("🎯  KIMM Z", accent="#9a6a4a", collapsed=True)
        self.kimm_z_panel = KIMMZPanel()
        self._sec_kimm.add_widget(self.kimm_z_panel)
        sidebar_v.addWidget(self._sec_kimm)

        # ACS 6축 키네마틱 스테이지 섹션 (기본 접힘)
        self._sec_acs = CollapsibleSection("⬡  ACS 6-AXIS KINEMATIC", accent="#7a6aaa", collapsed=True)
        self.acs_stage_panel = AcsStagePanel(ctrl=self.acs_ctrl)
        self._sec_acs.add_widget(self.acs_stage_panel)
        sidebar_v.addWidget(self._sec_acs)

        sidebar_v.addStretch(1)

        # ── #21 상태 복원 ──
        _s = QSettings("SpeAnalyze", "LiveTab")
        self._sec_cam  .set_collapsed(_s.value("sec/camera_collapsed",  False, type=bool))
        self._sec_motor.set_collapsed(_s.value("sec/motor_collapsed",   False, type=bool))
        self._sec_kimm .set_collapsed(_s.value("sec/kimm_collapsed",    True,  type=bool))
        self._sec_acs  .set_collapsed(_s.value("sec/acs_collapsed",     True,  type=bool))

        # 변경 시 자동 저장 연결
        self._sec_cam  .toggled.connect(lambda c: _s.setValue("sec/camera_collapsed", c))
        self._sec_motor.toggled.connect(lambda c: _s.setValue("sec/motor_collapsed", c))
        self._sec_kimm .toggled.connect(lambda c: _s.setValue("sec/kimm_collapsed", c))
        self._sec_acs  .toggled.connect(lambda c: _s.setValue("sec/acs_collapsed", c))

        left_scroll = QScrollArea()
        left_scroll.setWidget(sidebar)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setStyleSheet(
            "QScrollArea { border: none; background: #080e1e; }"
            "QScrollBar:vertical { width: 6px; background: #0a1020; }"
            "QScrollBar::handle:vertical { background: #1a3060; border-radius: 3px; }"
        )

        # 더블클릭 → dock 너비를 sidebar sizeHint에 맞게 자동 스냅
        self._left_scroll_ref = left_scroll
        self._sidebar_ref     = sidebar
        left_scroll.installEventFilter(self)

        self.dock_left = QDockWidget(self)
        self.dock_left.setObjectName("dock_left")
        
        # 드래그 가능하도록 헤더 바 추가
        self.dock_left.setTitleBarWidget(self._make_dock_header("⬤  CONTROLS"))
        self.dock_left.setWidget(left_scroll)
        
        self.dock_left.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_left)
        self.resizeDocks([self.dock_left], [600], Qt.Orientation.Horizontal)

        # ── Dock: Profile Plot (하단 좌) ──────────────────────────────
        self.plot_panel = PlotPanel("Profile")
        self.dock_plot = self._wrap_dock(
            "dock_plot", "📈  PROFILE", self.plot_panel,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )

        # ── Dock: Histogram (하단 우) ─────────────────────────────────
        self.hist_panel = HistogramPanel()
        self.dock_hist = self._wrap_dock(
            "dock_hist", "📊  HISTOGRAM", self.hist_panel,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.splitDockWidget(self.dock_plot, self.dock_hist, Qt.Orientation.Horizontal)

        # ── Dock: ROI List (우측, Log 아래) ──────────────────────────
        roi_container = QWidget()
        roi_container.setStyleSheet("background:#080e1e;")
        roi_v = QVBoxLayout(roi_container)
        roi_v.setContentsMargins(0, 0, 0, 0)
        roi_v.setSpacing(0)

        roi_hdr = QWidget()
        roi_hdr.setStyleSheet("background:#0c1428; border-bottom:1px solid #0f3460;")
        roi_hdr_row = QHBoxLayout(roi_hdr)
        roi_hdr_row.setContentsMargins(8, 3, 4, 3)
        lbl_roi_title = QLabel("ROI LIST")
        lbl_roi_title.setStyleSheet(
            f"color:#4a5a7a; font-family:'{_FC}'; font-size:{_FS_HDR};"
            " font-weight:bold; letter-spacing:2px;"
        )
        self._btn_del_roi = QPushButton("DEL")
        self._btn_del_roi.setFixedHeight(20)
        self._btn_del_roi.setToolTip("선택한 ROI 삭제 (Delete)")
        self._btn_del_roi.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:#304060; border:1px solid #1a2840;
                border-radius:2px; font-family:'{_FC}'; font-size:{_FS_HDR}; padding:0 6px; }}
            QPushButton:hover {{ color:#e94560; border-color:#e94560; }}
        """)
        self._btn_del_roi.clicked.connect(self._delete_selected_roi)
        btn_del_all = QPushButton("ALL")
        btn_del_all.setFixedHeight(20)
        btn_del_all.setToolTip("모든 ROI 삭제")
        btn_del_all.setStyleSheet(self._btn_del_roi.styleSheet())
        btn_del_all.clicked.connect(self._clear_all_rois)
        roi_hdr_row.addWidget(lbl_roi_title, 1)
        roi_hdr_row.addWidget(self._btn_del_roi)
        roi_hdr_row.addWidget(btn_del_all)
        roi_v.addWidget(roi_hdr)

        self._roi_list_widget = QListWidget()
        self._roi_list_widget.setStyleSheet(f"""
            QListWidget {{ background:#080e1e; border:none; color:#c0d0ff;
                font-family:'{_FC}'; font-size:{_FS_LOG}; }}
            QListWidget::item {{ padding:4px 8px; border-bottom:1px solid #0f2040; }}
            QListWidget::item:selected {{ background:#1a3a60; color:#4ecdc4; }}
            QListWidget::item:hover {{ background:#0f1f3a; }}
        """)
        self._roi_list_widget.itemClicked.connect(self._on_roi_list_click)
        roi_v.addWidget(self._roi_list_widget, 1)

        self.dock_roi = QDockWidget(self)
        self.dock_roi.setObjectName("dock_roi")
        self.dock_roi.setTitleBarWidget(self._make_dock_header("📐  ROI LIST"))
        self.dock_roi.setWidget(roi_container)
        self.dock_roi.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_roi)

        # 좌측 패널 고정폭
        self.dock_left.setMinimumWidth(320)
        self.dock_left.setMaximumWidth(500)

        self.resizeDocks([self.dock_plot], [200], Qt.Orientation.Vertical)

    def _setup_toolbar(self):
        tb = QToolBar("Live Toolbar")
        tb.setObjectName("live_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setStyleSheet(f"""
            QToolBar {{ background: #0a0f1e; border-bottom: 1px solid #0f3460; spacing: 4px; padding: 2px 6px; }}
            QToolButton {{ background: #0d1e38; color: #4ecdc4; border: 1px solid #1a4060;
                border-radius: 3px; padding: 3px 8px;
                font-family: '{_FC}'; font-size: {_FS_TOOLBAR}; }}
            QToolButton:hover {{ background: #1a3a60; }}
            QToolButton:checked {{ background: #1a3010; color: #4ecdc4; border-color: #2a6020; }}
        """)
        self.addToolBar(tb)

        # Dock 토글 버튼들
        for label, dock_attr in [
            ("⬤ Controls",   "dock_left"),
            ("📈 Profile",   "dock_plot"),
            ("📊 Histogram", "dock_hist"),
            ("📐 ROI List",  "dock_roi"),
        ]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(True)
            dock = getattr(self, dock_attr)
            act.triggered.connect(dock.setVisible)
            dock.visibilityChanged.connect(act.setChecked)
            tb.addAction(act)

        tb.addSeparator()

        act_fit = QAction("⊡ FIT", self)
        act_fit.setToolTip("이미지를 화면에 맞춤 (F)")
        act_fit.triggered.connect(lambda: self.image_viewer.autoRange())
        tb.addAction(act_fit)

        tb.addSeparator()

        self.act_roi_range = QAction("🎯 ROI RANGE", self)
        self.act_roi_range.setCheckable(True)
        self.act_roi_range.setToolTip("선택 영역(드래그) 내 픽셀 min/max로 컬러맵 범위 설정")
        self.act_roi_range.triggered.connect(self._on_roi_range_toggled)
        tb.addAction(self.act_roi_range)

        tb.addSeparator()

        # ── #4 FREEZE ──
        self._act_freeze = QAction("❄ FREEZE", self)
        self._act_freeze.setCheckable(True)
        self._act_freeze.setChecked(False)
        self._act_freeze.setToolTip("현재 프레임 고정 — 라이브 스트림을 멈추지 않고 분석 가능 (Space)")
        self._act_freeze.triggered.connect(self._toggle_freeze)
        tb.addAction(self._act_freeze)

        tb.addSeparator()

        # #23 SAVE — QAction 대신 QPushButton 위젯으로 강조
        _save_style = f"""
            QPushButton {{
                background: #0d2820; color: #4ecdc4;
                border: 1px solid #4ecdc4; border-radius: 3px;
                font-family: '{_FC}'; font-weight: bold; font-size: {_FS_TOOLBAR};
                padding: 3px 10px; min-width: 72px;
            }}
            QPushButton:hover {{ background: #1a4838; border-color: #6aefdc; }}
            QPushButton:pressed {{ background: #2a6048; }}
        """
        btn_save_tb = QPushButton("📍 SAVE")
        btn_save_tb.setToolTip("Image + Centroid + Motor Position 저장 (S)")
        btn_save_tb.setStyleSheet(_save_style)
        btn_save_tb.clicked.connect(self._save_bundle)
        tb.addWidget(btn_save_tb)

        # #14 라이브 SPE 저장
        btn_spe_tb = QPushButton("🔬 SPE")
        btn_spe_tb.setToolTip("현재 프레임을 SPE 파일로 저장 (P)")
        btn_spe_tb.setStyleSheet(_save_style.replace("#4ecdc4", "#ffe66d")
                                             .replace("#6aefdc", "#ffee88")
                                             .replace("#0d2820", "#1a1208")
                                             .replace("#1a4838", "#2a2010")
                                             .replace("#2a6048", "#3a3018"))
        btn_spe_tb.clicked.connect(self._save_live_spe)
        tb.addWidget(btn_spe_tb)

        tb.addSeparator()

        # #20 이미지 크기 표시
        self._lbl_imgsize = QLabel("—×—px")
        self._lbl_imgsize.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FS_SMALL}; padding: 0 4px;"
        )
        tb.addWidget(self._lbl_imgsize)

        # #12 줌 레벨 표시
        self._lbl_zoom = QLabel("🔍 100%")
        self._lbl_zoom.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FS_SMALL}; padding: 0 4px;"
        )
        tb.addWidget(self._lbl_zoom)

        # #13 ROI 크기 표시
        self._lbl_roi = QLabel("ROI: —")
        self._lbl_roi.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FS_SMALL}; padding: 0 4px;"
        )
        tb.addWidget(self._lbl_roi)

        tb.addSeparator()

        act_reset = QAction("🔄 RESET", self)
        act_reset.setToolTip("도킹 레이아웃 초기화 (Profile/Histogram 좌우 분할)")
        act_reset.triggered.connect(self.reset_layout)
        tb.addAction(act_reset)

    def _connect_signals(self):
        self.camera_panel.camera_scan_requested.connect(self._scan_cameras)
        self.camera_panel.camera_connect_requested.connect(self._connect_camera)
        self.camera_panel.camera_disconnect_requested.connect(self._disconnect_camera)
        self.camera_panel.camera_start_requested.connect(self._start_camera)
        self.camera_panel.camera_stop_requested.connect(self._stop_camera)
        self.camera_panel.snap_requested.connect(self._snap_image)
        self.camera_panel.bg_capture_requested.connect(self._capture_bg)
        self.camera_panel.log_message.connect(lambda m: self._log(m, "camera"))
        self.camera_panel.exposure_applied.connect(self.exposure_applied)

        self.motor_panel.log_message.connect(lambda m: self._log(m, "dev"))
        self.kimm_z_panel.log_message.connect(lambda m: self._log(m, "dev"))
        self.acs_stage_panel.log_message.connect(lambda m: self._log(m, "dev"))
        self.motor_panel.pre_move_info_cb = self._get_pre_move_info

        # 이미지 프로세서 / 오토포커스 / 스캔 등
        if hasattr(self, "af_tab"):
            self.af_tab.log_message.connect(lambda m: self._log(m, "calc"))
        if hasattr(self, "scan_tab"):
            self.scan_tab.log_message.connect(lambda m: self._log(m, "calc"))

        # ImageViewer → 플롯/히스토그램
        self.image_viewer.line_profile_updated.connect(
            lambda data, lbl: self.plot_panel.plot_line(data, lbl)
        )
        self.image_viewer.box_profile_updated.connect(
            lambda d1, d2, lbl: self.plot_panel.plot_two_lines(d1, d2, "X mean", "Y mean")
        )
        self.image_viewer.histogram_updated.connect(self.hist_panel.plot_histogram)
        self.image_viewer.colormap_changed.connect(self._on_viewer_colormap_changed)

        # #12 줌 레벨
        self.image_viewer._view.scale_changed.connect(self._on_zoom_changed)
        # #13 ROI 크기
        self.image_viewer._view.roi_drawn.connect(self._on_roi_size)
        # ROI 목록 패널 연동
        self.image_viewer._view.on_roi_added   = self._on_roi_added_to_list
        self.image_viewer._view.on_roi_selected = self._on_roi_selected_in_list

    def sync_exposure_ui(self, ms: float):
        """다른 탭에서 노출값 변경 시 UI만 업데이트 (카메라 재적용 안 함)."""
        self.camera_panel.spin_exposure.blockSignals(True)
        self.camera_panel.spin_exposure.setValue(ms)
        self.camera_panel.spin_exposure.blockSignals(False)

    # ── 카메라 제어 ───────────────────────────────────────────────────

    def _scan_cameras(self):
        """#9 스캔 결과 힌트 포함. SDK 스레드 제약으로 메인 스레드에서 실행."""
        cam_type = self.camera_panel.get_selected_camera_type()
        self._log(f"🔄 {cam_type} 스캔 중...", "camera")
        try:
            if cam_type == "HIKVISION":
                items = hik_devices()
            elif cam_type == "SIMULATED":
                items = sim_devices()
            else:
                items = picam_devices()
        except Exception as e:
            self._log(f"❌ 스캔 오류: {e}", "camera")
            items = []
        if items:
            self.camera_panel.populate_camera_list(items)
            self._log(f"✅ {len(items)}개 발견 ({cam_type})", "camera")
        else:
            self.camera_panel.populate_camera_list([])
            hints = {
                "HIKVISION":  "USB/네트워크 연결 확인",
                "SIMULATED":  "simulated.py 임포트 오류",
                "Picam":      "Picam 라이브러리/하드웨어 확인",
            }
            self._log(f"⚠️ 카메라 없음 — {hints.get(cam_type, '연결 확인')}", "camera")

    def _connect_camera(self, index: int):
        """#6 카메라 연결을 백그라운드 스레드에서 실행 — UI 응답 유지."""
        if self._conn_thread is not None and self._conn_thread.isRunning():
            self._log("⚠️ 연결 중...", "camera"); return

        # 다른 카메라가 연결되어 있으면 먼저 해제 후 재연결
        if self._camera is not None:
            new_type = self.camera_panel.get_selected_camera_type()
            cur_cls  = type(self._camera)
            _same_map = {
                "HIKVISION":  HikvisionCamera,
                "SIMULATED":  SimulatedCamera,
                "Picam":      PicamCamera,
            }
            if cur_cls is _same_map.get(new_type):
                self._log("⚠️ 이미 동일 카메라 연결됨", "camera"); return
            self._log(f"🔄 카메라 변경 — 기존 연결 해제 후 재연결...", "camera")
            self._pending_connect_index = index
            self._disconnect_camera()   # 완료 후 _on_disconnect_done → _connect_pending
            return

        cam_type = self.camera_panel.get_selected_camera_type()
        try:
            if cam_type == "HIKVISION":
                camera = HikvisionCamera(device_index=max(0, index))
            elif cam_type == "SIMULATED":
                camera = SimulatedCamera()
            else:
                camera = PicamCamera()
        except Exception as e:
            self._log(f"❌ 카메라 생성 실패: {e}", "camera"); return

        # 로딩 상태 표시
        self._log(f"🔄 {cam_type} 연결 중...", "camera")
        self._sec_cam._title_lbl.setText("📷  CAMERA  [ 연결 중… ]")
        self.camera_panel.setEnabled(False)

        # 백그라운드 연결
        self._conn_thread = QThread()
        self._conn_worker = _ConnectWorker(camera)
        self._conn_worker.moveToThread(self._conn_thread)
        self._conn_thread.started.connect(self._conn_worker.run)
        self._conn_worker.success.connect(self._on_connect_success)
        self._conn_worker.error.connect(self._on_connect_error)
        self._conn_worker.success.connect(lambda _: self._conn_thread.quit())
        self._conn_worker.error.connect(lambda _: self._conn_thread.quit())
        self._conn_thread.start()

    def _on_connect_success(self, camera: BaseCamera):
        """연결 성공 — 메인 스레드에서 실행됨."""
        self._camera = camera
        self.camera_panel.setEnabled(True)
        self.camera_panel.attach_camera(camera)
        cam_type = type(camera).__name__.replace("Camera", "")
        self._sec_cam._title_lbl.setText(f"📷  {cam_type.upper()}  ● LIVE")
        self._log(f"✅ {cam_type} 연결 완료", "camera")
        self.status_message.emit(f"{cam_type} 연결됨")
        self.camera_connected.emit(camera)

    def _on_connect_error(self, msg: str):
        """연결 실패 — 메인 스레드에서 실행됨."""
        self.camera_panel.setEnabled(True)
        self._sec_cam._title_lbl.setText("📷  CAMERA CONTROL")
        self._log(f"❌ 연결 실패: {msg}", "camera")

    def _disconnect_camera(self):
        """DISCONNECT 버튼 — stop_live + disconnect를 백그라운드에서 실행."""
        camera = self._camera
        if camera is None:
            return
        self._camera = None                    # 즉시 참조 해제 — 새 프레임 무시
        self.camera_panel.setEnabled(False)
        self.camera_panel.set_grabbing(False)
        self._log("🔄 연결 해제 중...", "camera")

        self._disc_thread = QThread()
        self._disc_worker = _DisconnectWorker(camera)
        self._disc_worker.moveToThread(self._disc_thread)
        self._disc_thread.started.connect(self._disc_worker.run)
        self._disc_worker.done.connect(self._on_disconnect_done)
        self._disc_worker.done.connect(lambda: self._disc_thread.quit())
        self._disc_thread.start()

    def _on_disconnect_done(self):
        """_DisconnectWorker 완료 후 메인 스레드에서 UI 정리."""
        self.camera_panel.setEnabled(True)
        self.camera_panel.detach_camera()
        self._proc.reset_buffer()
        self._sec_cam._title_lbl.setText("📷  CAMERA CONTROL")
        self._log("카메라 연결 해제", "camera")
        self.status_message.emit("카메라 해제")
        self.camera_disconnected.emit()
        # 카메라 변경 시 해제 완료 → 새 카메라로 바로 연결
        if self._pending_connect_index is not None:
            idx = self._pending_connect_index
            self._pending_connect_index = None
            self._connect_camera(idx)

    def _start_camera(self):
        if self._camera is None: return
        try:
            self._first_frame = True
            self._camera.start_live(self._on_new_frame)
            self.camera_panel.set_grabbing(True)
            self._log("▶ 카메라 시작", "camera")
            
            # [Phase] 라이브 프로그레스 타이머 설정 (노출이 길 경우)
            try:
                if hasattr(self._camera, '_get_frame_total_s'):
                    exp_ms = self._camera._get_frame_total_s() * 1000.0
                else:
                    exp_ms = self._camera.get_exposure_ms()
            except: exp_ms = 0
            
            if exp_ms > 100:
                self._live_total = exp_ms
                self._live_elapsed = 0
                if hasattr(self, '_live_timer_anim') and self._live_timer_anim.isActive():
                    self._live_timer_anim.stop()
                self._live_timer_anim = QTimer()
                self._live_timer_anim.setInterval(max(20, int(exp_ms / 50)))
                self._live_timer_anim.timeout.connect(self._on_live_progress_tick)
                self._live_timer_anim.start()
            else:
                self.camera_panel.bar_snap_progress.setValue(100)
        except Exception as e:
            self._log(f"❌ 시작 실패: {e}", "camera")

    def _stop_camera(self):
        """STOP 버튼 — stop_live()를 백그라운드에서 실행, UI는 즉시 갱신."""
        if self._camera is None: return
        self.camera_panel.set_grabbing(False)
        camera = self._camera
        
        if hasattr(self, '_live_timer_anim') and self._live_timer_anim.isActive():
            self._live_timer_anim.stop()
        self.camera_panel.bar_snap_progress.setValue(0)

        def _do_stop():
            try:
                camera.stop_live()
            except Exception: pass

        import threading
        t = threading.Thread(target=_do_stop, daemon=True, name="StopLive")
        t.start()
        self._log("■ 카메라 정지", "camera")

    def stop_live(self):
        """외부(Acquisition/Scan 탭) 호출 — 동기적으로 완료 보장."""
        if self._camera is None:
            self._was_live = False
            return
        self._was_live = self.camera_panel.btn_stop.isEnabled()  # grabbing 중이면 True
        try:
            self._camera.stop_live()
        except Exception:
            pass
        self.camera_panel.set_grabbing(False)

    def resume_live(self):
        """Acquisition/Scan 완료 후 — stop_live() 직전에 grabbing 중이었으면 재개."""
        if getattr(self, "_was_live", False) and self._camera is not None:
            self._start_camera()

    def _on_live_progress_tick(self):

        """라이브 모드 중 프로그레스바를 노출 주기에 맞춰 채움."""
        self._live_elapsed += self._live_timer_anim.interval()
        if self._live_elapsed >= self._live_total:
            self.camera_panel.bar_snap_progress.setValue(99)
        else:
            pct = int(100 * self._live_elapsed / max(self._live_total, 1))
            self.camera_panel.bar_snap_progress.setValue(pct)

    def _on_new_frame(self, raw: np.ndarray):
        """카메라로부터 새 프레임 수신 (백그라운드 스레드에서 호출됨)."""
        # 라이브 프로그레스 리셋 (메인 스레드에서 UI 갱신 필요하므로 타이머 값만 리셋)
        self._live_elapsed = 0
            
        if self._frozen:
            return
        self._on_frame(raw)

    def _snap_image(self):
        """단일 프레임 촬영 — 백그라운드 스레드에서 실행."""
        if self._camera is None: return
        if self._snap_thread is not None and self._snap_thread.isRunning():
            self._log("⚠️ 촬영 중...", "camera"); return

        self.camera_panel.btn_snap.setEnabled(False)
        self._log("📷 SNAP 촬영 중...", "camera")
        
        # [Progress Bar] LightField 스타일 노출 게이지 애니메이션 시작
        try:
            if hasattr(self._camera, '_get_frame_total_s'):
                exp_ms = self._camera._get_frame_total_s() * 1000.0
            else:
                exp_ms = self._camera.get_exposure_ms()
        except Exception:
            exp_ms = 1000.0
            
        if exp_ms > 100:
            self.camera_panel.bar_snap_progress.setValue(0)
            self._snap_elapsed = 0
            self._snap_total = exp_ms
            if hasattr(self, '_snap_timer_anim') and self._snap_timer_anim.isActive():
                self._snap_timer_anim.stop()
            self._snap_timer_anim = QTimer()
            self._snap_timer_anim.setInterval(max(20, int(exp_ms / 50)))
            self._snap_timer_anim.timeout.connect(self._on_snap_progress_tick)
            self._snap_timer_anim.start()
        else:
            self.camera_panel.bar_snap_progress.setValue(100)

        self._snap_thread = QThread()
        self._snap_worker = _SnapWorker(self._camera)
        self._snap_worker.moveToThread(self._snap_thread)
        self._snap_thread.started.connect(self._snap_worker.run)
        self._snap_worker.success.connect(self._on_snap_success)
        self._snap_worker.error.connect(self._on_snap_error)
        self._snap_worker.success.connect(lambda _: self._snap_thread.quit())
        self._snap_worker.error.connect(lambda _: self._snap_thread.quit())
        self._snap_thread.finished.connect(lambda: self.camera_panel.btn_snap.setEnabled(self._camera is not None))
        self._snap_thread.start()

    def _on_snap_progress_tick(self):
        """단일 촬영 중 프로그레스바를 부드럽게 채움."""
        self._snap_elapsed += self._snap_timer_anim.interval()
        if self._snap_elapsed >= self._snap_total:
            self.camera_panel.bar_snap_progress.setValue(100)
            self._snap_timer_anim.stop()
        else:
            pct = int(100 * self._snap_elapsed / max(self._snap_total, 1))
            self.camera_panel.bar_snap_progress.setValue(pct)

    def _on_snap_success(self, raw: np.ndarray):
        if hasattr(self, '_snap_timer_anim') and self._snap_timer_anim.isActive():
            self._snap_timer_anim.stop()
        self.camera_panel.bar_snap_progress.setValue(100)
        self._last_raw = raw
        self._viewer_raw = raw
        self._first_frame = True
        self._last_display_t = 0.0
        self._log("✅ SNAP 완료", "camera")
        self._proc_worker.submit(raw)

    def _on_snap_error(self, msg: str):
        if hasattr(self, '_snap_timer_anim') and self._snap_timer_anim.isActive():
            self._snap_timer_anim.stop()
        self.camera_panel.bar_snap_progress.setValue(0)
        self._log(f"❌ SNAP 실패: {msg}", "camera")


    def _capture_bg(self):
        from core.background_manager import BackgroundManager
        raw = self._last_raw
        if self._proc.capture_background(raw):
            if raw is not None:
                BackgroundManager.instance().set_frame(raw)
            self._log("📸 배경 캡처됨 (전체 탭 공유)", "camera")
        else:
            self._log("⚠ 버퍼 없음 — 카메라 실행 후 배경 캡처", "camera")

    # ── 프레임 처리 ───────────────────────────────────────────────────

    def _on_frame(self, raw: np.ndarray):
        """카메라 스레드에서 호출 — 즉시 반환, 처리는 _ProcessWorker가 담당."""
        self._proc_worker.submit(raw)

    def _on_processed(self, result, rgb: np.ndarray):
        """처리 워커 → GUI 스레드 (queued signal).
        centroid/raw 데이터는 항상 갱신, 화면 표시는 30fps 캡.
        """
        import time
        # cmap을 워커에 동기 (다음 프레임부터 적용)
        self._proc_worker.set_cmap(
            getattr(self.image_viewer, '_current_cmap', 'off')
        )
        # centroid / 저장용 데이터는 프레임 드롭 없이 항상 최신 유지
        self._last_raw     = result.raw
        self._last_display = result.display
        self._last_centroid = (
            result.centroid_x, result.centroid_y,
            result.brightness, result.fps,
            result.snr, result.frame_mean,
            result.saturated, result.sat_ratio,
        )
        # 화면 갱신: 최대 30fps (33ms 간격)
        now = time.monotonic()
        if now - self._last_display_t < 0.033:
            return
        self._last_display_t = now
        self._viewer_raw = result.raw   # 뷰어에 실제 그려지는 프레임과 1:1 대응
        # Range/colormap 기준은 display(uint8) 아니라 raw(원본 bit-depth)여야 한다.
        self._show_frame(rgb, result.raw)

    def _show_frame(self, rgb: np.ndarray, raw_display: Optional[np.ndarray] = None):
        """#4 Freeze: 고정 중이면 표시 갱신 안 함. #5 AutoFIT: 첫 프레임만 fit.
        raw_display: colormap 적용 전 원본 grayscale(가능하면 raw 원본 dtype 유지)
                     — _refresh_pixmap/_export_image/range slider 기준 데이터.
                     None이면 rgb를 그대로 저장 (정적 이미지 경로에서 사용).
        """
        if self._frozen:
            return
        h, w = rgb.shape[:2]
        self._lbl_imgsize.setText(f"{w}×{h}px")
        _, _, _, fps, _, _, sat, sat_r = self._last_centroid
        self.frame_stats_updated.emit(fps if fps else 0.0, w, h)
        _, _, _, _, _, _, sat, sat_r = self._last_centroid
        self.image_viewer.set_saturated(sat, sat_r)
        # 프로파일/룰러 계산 기준을 raw로 맞추기 위해 먼저 source를 갱신한다.
        if raw_display is not None:
            self.image_viewer.set_source_image(raw_display)
        else:
            self.image_viewer.set_source_image(rgb)
        self.image_viewer.set_live_frame(rgb, fit=self._first_frame)
        self._first_frame = False

    def _refresh_centroid_labels(self):
        cx, cy, br, fps, snr, mean, sat, sat_r = self._last_centroid
        self.camera_panel.update_centroid(
            cx, cy, br, fps,
            snr=snr, mean=mean,
            saturated=sat, sat_ratio=sat_r,
        )

    # ── #4 Freeze ────────────────────────────────────────────────────

    def _toggle_freeze(self, checked: bool):
        self._frozen = checked
        if checked:
            self._act_freeze.setText("❄ FROZEN")
            self._log("❄ 프레임 고정 — 현재 프레임 유지", "calc")
        else:
            self._act_freeze.setText("❄ FREEZE")
            self._log("▶ 프레임 재개", "calc")

    # ── #8 키보드 단축키 ─────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            checked = not self._frozen
            self._act_freeze.setChecked(checked)
            self._toggle_freeze(checked)
        elif key == Qt.Key.Key_S:
            self._save_bundle()
        elif key == Qt.Key.Key_P:
            self._save_live_spe()
        elif key == Qt.Key.Key_F:
            self.image_viewer.autoRange()
        elif key == Qt.Key.Key_R:
            self._clear_all_rois()
            self._log("ROI 초기화", "calc")
        else:
            super().keyPressEvent(event)

    def _on_roi_range_toggled(self, checked: bool):
        if self.image_viewer.btn_roi_range.isChecked() != checked:
            self.image_viewer.btn_roi_range.setChecked(checked)
        if hasattr(self.image_viewer, '_on_roi_range_toggled'):
            self.image_viewer._on_roi_range_toggled(checked)

    # ── #12 줌 레벨 표시 ─────────────────────────────────────────────

    def _on_zoom_changed(self, scale: float, _x: float, _y: float):
        pct = int(scale * 100)
        self._lbl_zoom.setText(f"🔍 {pct}%")

    # ── #13 ROI 크기 표시 ────────────────────────────────────────────

    # ── ROI 목록 패널 ─────────────────────────────────────────────────

    def _roi_label(self, roi) -> str:
        """ROI 객체 → 목록에 표시할 텍스트."""
        try:
            (x0, y0), (x1, y1) = roi.pts[0], roi.pts[1]
            rt = roi.roi_type
            if rt == 'Line':
                l = np.hypot(x1 - x0, y1 - y0)
                return f"#{roi.roi_id}  Line  {l:.0f}px  ({x0:.0f},{y0:.0f})→({x1:.0f},{y1:.0f})"
            else:
                w, h = abs(x1 - x0), abs(y1 - y0)
                tag = "Hist" if rt == 'Hist' else "Box"
                return f"#{roi.roi_id}  {tag}  {w:.0f}×{h:.0f}  @({x0:.0f},{y0:.0f})"
        except Exception:
            return f"#{getattr(roi, 'roi_id', '?')}  {getattr(roi, 'roi_type', '?')}"

    def _on_roi_added_to_list(self, roi):
        """ROI 드로우 완료 시 목록에 항목 추가 후 ROI List 탭 활성화."""
        item = QListWidgetItem(self._roi_label(roi))
        item.setData(0x100, roi.roi_id)   # Qt.UserRole = 0x100
        self._roi_list_widget.addItem(item)
        self._roi_list_widget.setCurrentItem(item)
        self.dock_roi.show()
        self.dock_roi.raise_()

    def _on_roi_selected_in_list(self, roi_id):
        """ImageViewer 에서 ROI 선택 시 목록도 동기화."""
        for i in range(self._roi_list_widget.count()):
            item = self._roi_list_widget.item(i)
            if item.data(0x100) == roi_id:
                self._roi_list_widget.setCurrentItem(item)
                return

    def _log(self, msg: str, category: str = "sys"):
        """로깅 인터페이스: 메인 윈도우의 전역 로그 시스템을 호출하거나 직접 로그 기록."""
        from core.logger import sys_logger, dev_logger, cam_logger, calc_logger
        logger = sys_logger
        if category == "dev": logger = dev_logger
        elif category == "camera": logger = cam_logger
        elif category == "calc": logger = calc_logger
        
        logger.info(msg)

    def _on_roi_list_click(self, item: QListWidgetItem):
        """목록 클릭 → ImageViewer 에서 해당 ROI 선택."""
        roi_id = item.data(0x100)
        if roi_id is not None:
            self.image_viewer._view._select_roi(roi_id)

    def _delete_selected_roi(self):
        """목록에서 선택한 ROI 삭제."""
        item = self._roi_list_widget.currentItem()
        if item is None:
            return
        roi_id = item.data(0x100)
        self.image_viewer._view.delete_roi(roi_id)
        row = self._roi_list_widget.row(item)
        self._roi_list_widget.takeItem(row)
        self._lbl_roi.setText("ROI: —")

    def _clear_all_rois(self):
        """모든 ROI 삭제."""
        self.image_viewer._view.delete_all_rois()
        self._roi_list_widget.clear()
        self._lbl_roi.setText("ROI: —")

    def _get_pre_move_info(self):
        """모터 이동 직전 콜백 — (cx, cy, [M1,M2,M3,M4]) 반환."""
        cx, cy = self._last_centroid[0], self._last_centroid[1]
        positions = self.motor_panel.get_positions()   # [p1,p2,p3,p4] or None
        return cx, cy, positions

    def _on_roi_size(self, mode: str, pts: list):
        try:
            if mode == 'line' and len(pts) >= 2:
                (x0, y0), (x1, y1) = pts[0], pts[1]
                length = np.hypot(x1 - x0, y1 - y0)
                self._lbl_roi.setText(f"ROI: Line {length:.0f}px")
            elif mode in ('box', 'histogram') and len(pts) >= 2:
                (x0, y0), (x1, y1) = pts[0], pts[1]
                w = abs(x1 - x0)
                h = abs(y1 - y0)
                self._lbl_roi.setText(f"ROI: {w:.0f}×{h:.0f}px")
        except Exception:
            pass

    def get_last_raw(self) -> Optional[np.ndarray]:
        """현재 뷰어에 표시 중인 최신 원본 이미지를 반환한다."""
        return self._viewer_raw

    # ── #14 라이브 SPE 저장 ──────────────────────────────────────────

    def _save_live_spe(self):
        # 뷰어에 실제 표시된 프레임 사용 — 카메라 최신 프레임(last_raw)이 아님
        raw = self._viewer_raw
        if raw is None:
            self._log("⚠️ 저장할 프레임 없음 (뷰어에 이미지 없음)"); return
        save_dir = "Live_Captures"
        os.makedirs(save_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        cam_name = (type(self._camera).__name__.replace("Camera", "")
                    if self._camera else "Live")
        path = os.path.join(save_dir, f"live_{cam_name}_{ts}.spe")
        try:
            exp_ms = 0.0
            if self._camera is not None:
                try:
                    exp_ms = self._camera.get_exposure_ms()
                except Exception:
                    pass
            if isinstance(self._camera, PicamCamera):
                self._camera.save_as_spe(path, [raw], exposure_ms=exp_ms)
            else:
                save_spe(
                    path, [raw],
                    exposure_ms=exp_ms,
                    camera_name=cam_name,
                    software="SpeAnalyze-Live",
                )
            self._log(f"🔬 SPE 저장: {path}")
        except Exception as e:
            self._log(f"❌ SPE 저장 실패: {e}")

    # ── 저장 ─────────────────────────────────────────────────────────

    def _save_bundle(self):
        if self._viewer_raw is None:
            self._log("⚠️ 저장할 이미지 없음 (뷰어에 이미지 없음)"); return

        now = datetime.now()
        ts_full = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        ts_file = now.strftime("%H%M%S_%f")[:-3]
        save_dir = "Live_Captures"
        os.makedirs(save_dir, exist_ok=True)

        cx, cy, br, *_ = self._last_centroid
        xstr = f"{cx:.1f}".replace(".", "p") if cx is not None else "X"
        ystr = f"{cy:.1f}".replace(".", "p") if cy is not None else "Y"

        raw_name  = f"R_{ts_file}_X{xstr}_Y{ystr}.bmp"
        disp_name = f"D_{ts_file}_X{xstr}_Y{ystr}.bmp"

        if _CV2_OK:
            cv2.imwrite(os.path.join(save_dir, raw_name), self._viewer_raw)
            disp_img = self._last_display if self._last_display is not None else self._viewer_raw
            cv2.imwrite(os.path.join(save_dir, disp_name), disp_img)

        motor_pos = self.motor_panel.get_positions()

        file_exists = os.path.isfile(self._csv_path)
        with open(self._csv_path, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "Timestamp", "cX", "cY", "Brightness",
                    "Motor1", "Motor2", "Motor3", "Motor4",
                    "RawImg", "DisplayImg"
                ])
            writer.writerow([ts_full, cx, cy, br, *motor_pos, raw_name, disp_name])

        self._log(
            f"💾 저장: {ts_file}<br>"
            f"&nbsp;&nbsp;Centroid=({cx}, {cy})  Motors={motor_pos}"
        )



    # ── 설정 / 정리 ───────────────────────────────────────────────────

    def _save_settings(self):
        s = QSettings("SpeAnalyze", "LiveTab")
        s.setValue("dockState", self.saveState())
        s.sync()

    def _restore_settings(self):
        s = QSettings("SpeAnalyze", "LiveTab")
        state = s.value("dockState")
        if state:
            self.restoreState(state)

    def reset_layout(self):
        """도킹 레이아웃을 초기 상태(Profile/Histogram 좌우 분할 등)로 복구합니다."""
        s = QSettings("SpeAnalyze", "LiveTab")
        s.remove("dockState")
        
        # 모든 독 보이기
        for d in [self.dock_left, self.dock_plot, self.dock_hist, self.dock_roi]:
            d.setVisible(True)
            d.setFloating(False)
            
        # 기본 위치 재배치
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_left)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_plot)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_hist)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_roi)
        
        # 하단 좌우 분할 강제
        self.splitDockWidget(self.dock_plot, self.dock_hist, Qt.Orientation.Horizontal)
        
        # 크기 조정
        self.resizeDocks([self.dock_left], [400], Qt.Orientation.Horizontal)
        self._log("🔄 도킹 레이아웃 초기화 완료", "sys")

    def cleanup(self):
        self._centroid_timer.stop()
        # 앱 종료 시 — 비동기 워커를 기다리지 않고 직접 동기 정리
        if self._camera:
            try: self._camera.stop_live()
            except Exception: pass
            try: self._camera.disconnect()
            except Exception: pass
            self._camera = None
        self._proc_worker.stop()
        for t in (self._conn_thread, self._disc_thread):
            if t and t.isRunning():
                t.quit()
                t.wait(2000)
        self.motor_panel.cleanup()
