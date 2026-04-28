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
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QToolBar,
    QScrollArea, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QSizePolicy,
    QApplication, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, QTimer, QSize, QSettings, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QAction

from core.camera.base import BaseCamera
from core.camera.hikvision import HikvisionCamera, list_devices as hik_devices
from core.camera.picamp import PicamCamera, list_devices as picam_devices
from core.image_processor import ImageProcessor
from core.spe_writer import save_spe   # #14 라이브 SPE 저장
from ui.image_viewer import ImageViewer
from ui.plot_panel import PlotPanel, HistogramPanel
from ui.live.camera_panel import CameraControlPanel
from ui.live.motor_panel import MotorPanel

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


# ── 카메라 연결 백그라운드 워커 ──────────────────────────────────────────────

class _SnapWorker(QObject):
    """snap() 1장 촬영을 백그라운드에서 실행 — UI 멈춤 방지."""
    success = pyqtSignal(object)   # np.ndarray
    error   = pyqtSignal(str)

    def __init__(self, cam: BaseCamera):
        super().__init__()
        self._cam = cam

    def run(self):
        try:
            frame = self._cam.snap()
            self.success.emit(np.asarray(frame))
        except Exception as e:
            self.error.emit(str(e))


class _DisconnectWorker(QObject):
    """stop_live() + disconnect()를 백그라운드에서 순차 실행."""
    done = pyqtSignal()

    def __init__(self, cam: BaseCamera):
        super().__init__()
        self._cam = cam

    def run(self):
        try:
            self._cam.stop_live()
        except Exception:
            pass
        try:
            self._cam.disconnect()
        except Exception:
            pass
        self.done.emit()


class _ConnectWorker(QObject):
    """카메라 connect()를 백그라운드에서 실행 — UI 멈춤 방지."""
    success = pyqtSignal(object)   # BaseCamera
    error   = pyqtSignal(str)

    def __init__(self, cam: BaseCamera):
        super().__init__()
        self._cam = cam

    def run(self):
        try:
            self._cam.connect()
            self.success.emit(self._cam)
        except Exception as e:
            self.error.emit(str(e))


# ── 이미지 처리 백그라운드 워커 ─────────────────────────────────────────────────

def _build_rgb(result, cmap: str, show_binary: bool) -> np.ndarray:
    """ProcessedFrame → display RGB. 백그라운드 스레드에서 호출됨."""
    disp = result.display
    if cmap and cmap != 'off' and disp.ndim == 2 and not show_binary:
        from ui.image_viewer import apply_colormap
        rgba = apply_colormap(disp, cmap)
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
                    rgb = _build_rgb(result, self._cmap, show_binary)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.menuBar().setVisible(False)

        self._cam: Optional[BaseCamera] = None
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

        # #21 독 레이아웃 복원
        _s = QSettings("SpeAnalyze", "LiveTab")
        _state = _s.value("dockState")
        if _state is not None:
            self.restoreState(_state)

        self._centroid_timer = QTimer()
        self._centroid_timer.setInterval(200)
        self._centroid_timer.timeout.connect(self._refresh_centroid_labels)
        self._centroid_timer.start()

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── 중앙: ImageViewer ─────────────────────────────────────────
        self.image_viewer = ImageViewer()
        self.setCentralWidget(self.image_viewer)

        # ── Dock: Camera Control (좌측 상단) ──────────────────────────
        self.cam_panel = CameraControlPanel(self._proc)
        cam_scroll = QScrollArea()
        cam_scroll.setWidget(self.cam_panel)
        cam_scroll.setWidgetResizable(True)
        cam_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cam_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.dock_cam = QDockWidget("📷  Camera Control", self)
        self.dock_cam.setWidget(cam_scroll)
        self.dock_cam.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_cam)
        self.dock_cam.setObjectName("dock_cam")

        # ── Dock: Motors (좌측 하단) ──────────────────────────────────
        self.motor_panel = MotorPanel()
        motor_scroll = QScrollArea()
        motor_scroll.setWidget(self.motor_panel)
        motor_scroll.setWidgetResizable(True)
        motor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        motor_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.dock_motor = QDockWidget("⚙️  Motors", self)
        self.dock_motor.setWidget(motor_scroll)
        self.dock_motor.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_motor)
        self.splitDockWidget(self.dock_cam, self.dock_motor, Qt.Orientation.Vertical)
        self.dock_motor.setObjectName("dock_motor")

        # ── Dock: Profile Plot (하단 좌) ──────────────────────────────
        self.plot_panel = PlotPanel("Profile")
        self.dock_plot = QDockWidget("📈  Profile", self)
        self.dock_plot.setWidget(self.plot_panel)
        self.dock_plot.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_plot)
        self.dock_plot.setObjectName("dock_plot")

        # ── Dock: Histogram (하단 우) ─────────────────────────────────
        self.hist_panel = HistogramPanel()
        self.dock_hist = QDockWidget("📊  Histogram", self)
        self.dock_hist.setWidget(self.hist_panel)
        self.dock_hist.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_hist)
        self.splitDockWidget(self.dock_plot, self.dock_hist, Qt.Orientation.Horizontal)
        self.dock_hist.setObjectName("dock_hist")

        # ── Dock: System Log (우측) ── #1 타임스탬프 #2 클리어버튼 ────
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        # 로그 헤더 (클리어 버튼)
        log_header = QWidget()
        log_header.setStyleSheet("background: #0c1428; border-bottom: 1px solid #0f3460;")
        hdr_row = QHBoxLayout(log_header)
        hdr_row.setContentsMargins(8, 3, 4, 3)
        lbl_log_title = QLabel("SYSTEM LOG")
        lbl_log_title.setStyleSheet(
            "color: #4a5a7a; font-family: 'Courier New'; font-size: 10px; font-weight: bold; letter-spacing: 2px;"
        )
        btn_clear_log = QPushButton("CLEAR")
        btn_clear_log.setFixedHeight(20)
        btn_clear_log.setStyleSheet("""
            QPushButton { background: transparent; color: #304060; border: 1px solid #1a2840;
                border-radius: 2px; font-family: 'Courier New'; font-size: 9px; padding: 0 6px; }
            QPushButton:hover { color: #e94560; border-color: #e94560; }
        """)
        btn_clear_log.clicked.connect(lambda: self.log_display.clear())
        hdr_row.addWidget(lbl_log_title, 1)
        hdr_row.addWidget(btn_clear_log)
        log_layout.addWidget(log_header)

        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet(f"""
            QTextEdit {{ background: #080e1e; border: none;
                color: #00cc88; font-family: 'Courier New'; font-size: {{self.log_font_size}}px; }}
        """)
        self.log_font_size = 12  # Default font size for the log display
        log_layout.addWidget(self.log_display, 1)

        self.dock_log = QDockWidget("🖥  System Log", self)
        self.dock_log.setWidget(log_container)
        self.dock_log.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_log)
        self.dock_log.setObjectName("dock_log")

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
            "color:#4a5a7a; font-family:'Courier New'; font-size:10px;"
            " font-weight:bold; letter-spacing:2px;"
        )
        self._btn_del_roi = QPushButton("DEL")
        self._btn_del_roi.setFixedHeight(20)
        self._btn_del_roi.setToolTip("선택한 ROI 삭제 (Delete)")
        self._btn_del_roi.setStyleSheet("""
            QPushButton { background:transparent; color:#304060; border:1px solid #1a2840;
                border-radius:2px; font-family:'Courier New'; font-size:9px; padding:0 6px; }
            QPushButton:hover { color:#e94560; border-color:#e94560; }
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
        self._roi_list_widget.setStyleSheet("""
            QListWidget { background:#080e1e; border:none; color:#c0d0ff;
                font-family:'Courier New'; font-size:11px; }
            QListWidget::item { padding:4px 8px; border-bottom:1px solid #0f2040; }
            QListWidget::item:selected { background:#1a3a60; color:#4ecdc4; }
            QListWidget::item:hover { background:#0f1f3a; }
        """)
        self._roi_list_widget.itemClicked.connect(self._on_roi_list_click)
        roi_v.addWidget(self._roi_list_widget, 1)

        self.dock_roi = QDockWidget("📐  ROI List", self)
        self.dock_roi.setWidget(roi_container)
        self.dock_roi.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_roi)
        self.tabifyDockWidget(self.dock_log, self.dock_roi)
        self.dock_roi.setObjectName("dock_roi")

        # 기본 크기 힌트
        self.resizeDocks(
            [self.dock_cam, self.dock_motor], [350, 350], Qt.Orientation.Vertical
        )
        self.dock_cam.setMinimumWidth(500)
        self.dock_motor.setMinimumWidth(500)

        self.resizeDocks([self.dock_plot], [220], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_log], [240], Qt.Orientation.Horizontal)

    def _setup_toolbar(self):
        tb = QToolBar("Live Toolbar")
        tb.setObjectName("live_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setStyleSheet("""
            QToolBar { background: #0a0f1e; border-bottom: 1px solid #0f3460; spacing: 4px; padding: 2px 6px; }
            QToolButton { background: #0d1e38; color: #4ecdc4; border: 1px solid #1a4060;
                border-radius: 3px; padding: 3px 8px;
                font-family: 'Courier New'; font-size: 11px; }
            QToolButton:hover { background: #1a3a60; }
            QToolButton:checked { background: #1a3010; color: #4ecdc4; border-color: #2a6020; }
        """)
        self.addToolBar(tb)

        # Dock 토글 버튼들
        for label, dock_attr in [
            ("📷 Camera",    "dock_cam"),
            ("⚙️ Motors",    "dock_motor"),
            ("📈 Profile",   "dock_plot"),
            ("📊 Histogram", "dock_hist"),
            ("🖥 Log",       "dock_log"),
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

        # ── #4 FREEZE ──
        self._act_freeze = QAction("❄ FREEZE", self)
        self._act_freeze.setCheckable(True)
        self._act_freeze.setChecked(False)
        self._act_freeze.setToolTip("현재 프레임 고정 — 라이브 스트림을 멈추지 않고 분석 가능 (Space)")
        self._act_freeze.triggered.connect(self._toggle_freeze)
        tb.addAction(self._act_freeze)

        tb.addSeparator()

        # #23 SAVE — QAction 대신 QPushButton 위젯으로 강조
        _save_style = """
            QPushButton {
                background: #0d2820; color: #4ecdc4;
                border: 1px solid #4ecdc4; border-radius: 3px;
                font-family: 'Courier New'; font-weight: bold; font-size: 11px;
                padding: 3px 10px; min-width: 72px;
            }
            QPushButton:hover { background: #1a4838; border-color: #6aefdc; }
            QPushButton:pressed { background: #2a6048; }
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
            "color: #4a6a8a; font-family: 'Courier New'; font-size: 10px; padding: 0 4px;"
        )
        tb.addWidget(self._lbl_imgsize)

        # #12 줌 레벨 표시
        self._lbl_zoom = QLabel("🔍 100%")
        self._lbl_zoom.setStyleSheet(
            "color: #4a6a8a; font-family: 'Courier New'; font-size: 10px; padding: 0 4px;"
        )
        tb.addWidget(self._lbl_zoom)

        # #13 ROI 크기 표시
        self._lbl_roi = QLabel("ROI: —")
        self._lbl_roi.setStyleSheet(
            "color: #4a6a8a; font-family: 'Courier New'; font-size: 10px; padding: 0 4px;"
        )
        tb.addWidget(self._lbl_roi)

    def _connect_signals(self):
        self.cam_panel.camera_scan_requested.connect(self._scan_cameras)
        self.cam_panel.camera_connect_requested.connect(self._connect_camera)
        self.cam_panel.camera_disconnect_requested.connect(self._disconnect_camera)
        self.cam_panel.camera_start_requested.connect(self._start_camera)
        self.cam_panel.camera_stop_requested.connect(self._stop_camera)
        self.cam_panel.snap_requested.connect(self._snap_image)
        self.cam_panel.bg_capture_requested.connect(self._capture_bg)
        self.cam_panel.log_message.connect(self._log)

        self.motor_panel.log_message.connect(self._log)

        # ImageViewer → 플롯/히스토그램
        self.image_viewer.line_profile_updated.connect(
            lambda data, lbl: self.plot_panel.plot_line(data, lbl)
        )
        self.image_viewer.box_profile_updated.connect(
            lambda d1, d2, lbl: self.plot_panel.plot_two_lines(d1, d2, "X mean", "Y mean")
        )
        self.image_viewer.histogram_updated.connect(self.hist_panel.plot_histogram)

        # #12 줌 레벨
        self.image_viewer._view.scale_changed.connect(self._on_zoom_changed)
        # #13 ROI 크기
        self.image_viewer._view.roi_drawn.connect(self._on_roi_size)
        # ROI 목록 패널 연동
        self.image_viewer._view.on_roi_added   = self._on_roi_added_to_list
        self.image_viewer._view.on_roi_selected = self._on_roi_selected_in_list

    # ── 카메라 제어 ───────────────────────────────────────────────────

    def _scan_cameras(self):
        """#9 스캔 결과 힌트 포함. SDK 스레드 제약으로 메인 스레드에서 실행."""
        cam_type = self.cam_panel.get_selected_camera_type()
        self._log(f"🔄 {cam_type} 스캔 중...")
        try:
            items = hik_devices() if cam_type == "HIKVISION" else picam_devices()
        except Exception as e:
            self._log(f"❌ 스캔 오류: {e}")
            items = []
        if items:
            self.cam_panel.populate_camera_list(items)
            self._log(f"✅ {len(items)}개 발견 ({cam_type})")
        else:
            self.cam_panel.populate_camera_list([])
            hint = ("USB/네트워크 연결 확인"
                    if cam_type == "HIKVISION"
                    else "Picam 라이브러리/하드웨어 확인")
            self._log(f"⚠️ 카메라 없음 — {hint}")

    def _connect_camera(self, index: int):
        """#6 카메라 연결을 백그라운드 스레드에서 실행 — UI 응답 유지."""
        if self._conn_thread is not None and self._conn_thread.isRunning():
            self._log("⚠️ 연결 중..."); return

        # 다른 카메라가 연결되어 있으면 먼저 해제 후 재연결
        if self._cam is not None:
            new_type = self.cam_panel.get_selected_camera_type()
            cur_type = type(self._cam).__name__
            # 같은 타입이면 재연결 불필요
            same = (new_type == "HIKVISION" and "Hikvision" in cur_type) or \
                   (new_type != "HIKVISION" and "Hikvision" not in cur_type)
            if same:
                self._log("⚠️ 이미 동일 카메라 연결됨"); return
            self._log(f"🔄 카메라 변경 — 기존 연결 해제 후 재연결...")
            self._pending_connect_index = index
            self._disconnect_camera()   # 완료 후 _on_disconnect_done → _connect_pending
            return

        cam_type = self.cam_panel.get_selected_camera_type()
        try:
            cam = HikvisionCamera(device_index=max(0, index)) \
                  if cam_type == "HIKVISION" else PicamCamera()
        except Exception as e:
            self._log(f"❌ 카메라 생성 실패: {e}"); return

        # 로딩 상태 표시
        self._log(f"🔄 {cam_type} 연결 중...")
        self.dock_cam.setWindowTitle("📷  Camera Control  [연결 중…]")
        self.cam_panel.setEnabled(False)

        # 백그라운드 연결
        self._conn_thread = QThread()
        self._conn_worker = _ConnectWorker(cam)
        self._conn_worker.moveToThread(self._conn_thread)
        self._conn_thread.started.connect(self._conn_worker.run)
        self._conn_worker.success.connect(self._on_connect_success)
        self._conn_worker.error.connect(self._on_connect_error)
        self._conn_worker.success.connect(lambda _: self._conn_thread.quit())
        self._conn_worker.error.connect(lambda _: self._conn_thread.quit())
        self._conn_thread.start()

    def _on_connect_success(self, cam: BaseCamera):
        """연결 성공 — 메인 스레드에서 실행됨."""
        self._cam = cam
        self.cam_panel.setEnabled(True)
        self.cam_panel.attach_camera(cam)
        cam_type = type(cam).__name__.replace("Camera", "")
        self.dock_cam.setWindowTitle(f"📷  {cam_type}  ● CONNECTED")
        self._log(f"✅ {cam_type} 연결 완료")
        self.status_message.emit(f"{cam_type} 연결됨")
        self.camera_connected.emit(cam)

    def _on_connect_error(self, msg: str):
        """연결 실패 — 메인 스레드에서 실행됨."""
        self.cam_panel.setEnabled(True)
        self.dock_cam.setWindowTitle("📷  Camera Control")
        self._log(f"❌ 연결 실패: {msg}")

    def _disconnect_camera(self):
        """DISCONNECT 버튼 — stop_live + disconnect를 백그라운드에서 실행."""
        cam = self._cam
        if cam is None:
            return
        self._cam = None                    # 즉시 참조 해제 — 새 프레임 무시
        self.cam_panel.setEnabled(False)
        self.cam_panel.set_grabbing(False)
        self._log("🔄 연결 해제 중...")

        self._disc_thread = QThread()
        self._disc_worker = _DisconnectWorker(cam)
        self._disc_worker.moveToThread(self._disc_thread)
        self._disc_thread.started.connect(self._disc_worker.run)
        self._disc_worker.done.connect(self._on_disconnect_done)
        self._disc_worker.done.connect(lambda: self._disc_thread.quit())
        self._disc_thread.start()

    def _on_disconnect_done(self):
        """_DisconnectWorker 완료 후 메인 스레드에서 UI 정리."""
        self.cam_panel.setEnabled(True)
        self.cam_panel.detach_camera()
        self._proc.reset_buffer()
        self.dock_cam.setWindowTitle("📷  Camera Control")
        self._log("카메라 연결 해제")
        self.status_message.emit("카메라 해제")
        self.camera_disconnected.emit()
        # 카메라 변경 시 해제 완료 → 새 카메라로 바로 연결
        if self._pending_connect_index is not None:
            idx = self._pending_connect_index
            self._pending_connect_index = None
            self._connect_camera(idx)

    def _start_camera(self):
        if self._cam is None: return
        try:
            self._first_frame = True
            self._cam.start_live(self._on_frame)
            self.cam_panel.set_grabbing(True)
            self._log("▶ 카메라 시작")
        except Exception as e:
            self._log(f"❌ 시작 실패: {e}")

    def _stop_camera(self):
        """STOP 버튼 — stop_live()를 백그라운드에서 실행, UI는 즉시 갱신."""
        if self._cam is None: return
        self.cam_panel.set_grabbing(False)  # 버튼 상태 즉시 갱신
        cam = self._cam

        def _do_stop():
            try:
                cam.stop_live()
            except Exception:
                pass

        import threading
        t = threading.Thread(target=_do_stop, daemon=True, name="StopLive")
        t.start()
        self._log("■ 카메라 정지")

    def stop_live(self):
        """외부(Acquisition 탭) 호출 — 동기적으로 완료 보장."""
        if self._cam is None: return
        try:
            self._cam.stop_live()
        except Exception:
            pass
        self.cam_panel.set_grabbing(False)

    def _snap_image(self):
        """단일 프레임 촬영 — 백그라운드 스레드에서 실행."""
        if self._cam is None:
            return
        if self._snap_thread is not None and self._snap_thread.isRunning():
            self._log("⚠️ 촬영 중..."); return

        self.cam_panel.btn_snap.setEnabled(False)
        self._log("📷 SNAP 촬영 중...")
        self._snap_thread = QThread()
        self._snap_worker = _SnapWorker(self._cam)
        self._snap_worker.moveToThread(self._snap_thread)
        self._snap_thread.started.connect(self._snap_worker.run)
        self._snap_worker.success.connect(self._on_snap_success)
        self._snap_worker.error.connect(self._on_snap_error)
        self._snap_worker.success.connect(lambda _: self._snap_thread.quit())
        self._snap_worker.error.connect(lambda _: self._snap_thread.quit())
        self._snap_thread.finished.connect(lambda: self.cam_panel.btn_snap.setEnabled(self._cam is not None))
        self._snap_thread.start()

    def _on_snap_success(self, raw: np.ndarray):
        self._last_raw = raw
        self._viewer_raw = raw         # 스냅은 즉시 뷰어에 표시됨
        self._first_frame = True       # snap 결과는 항상 FIT
        self._last_display_t = 0.0     # 30fps 캡 우회 — 즉시 표시
        self._log("✅ SNAP 완료")
        # _proc_worker에 위임 → 메인 스레드 블로킹 없음
        # 결과는 _on_processed() 에서 수신
        self._proc_worker.submit(raw)

    def _on_snap_error(self, msg: str):
        self._log(f"❌ SNAP 실패: {msg}")

    def _capture_bg(self):
        self._proc.capture_background(self._last_raw)
        self._log("📸 배경 캡처됨")

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
        self._show_frame(rgb, result.display)

    def _show_frame(self, rgb: np.ndarray, raw_display: Optional[np.ndarray] = None):
        """#4 Freeze: 고정 중이면 표시 갱신 안 함. #5 AutoFIT: 첫 프레임만 fit.
        raw_display: colormap 적용 전 원본 grayscale — _refresh_pixmap/_export_image용.
                     None이면 rgb를 그대로 저장 (정적 이미지 경로에서 사용).
        """
        if self._frozen:
            return
        h, w = rgb.shape[:2]
        self._lbl_imgsize.setText(f"{w}×{h}px")
        _, _, _, _, _, _, sat, sat_r = self._last_centroid
        self.image_viewer.set_saturated(sat, sat_r)
        self.image_viewer.set_live_frame(rgb, fit=self._first_frame)
        # set_live_frame이 _current_image에 RGB를 저장하므로, 원본 grayscale로 덮어써
        # colormap 변경 시 이중 적용 및 export 오류를 방지한다.
        if raw_display is not None:
            self.image_viewer.set_source_image(raw_display)
        self._first_frame = False

    def _refresh_centroid_labels(self):
        cx, cy, br, fps, snr, mean, sat, sat_r = self._last_centroid
        self.cam_panel.update_centroid(
            cx, cy, br, fps,
            snr=snr, mean=mean,
            saturated=sat, sat_ratio=sat_r,
        )

    # ── #4 Freeze ────────────────────────────────────────────────────

    def _toggle_freeze(self, checked: bool):
        self._frozen = checked
        if checked:
            self._act_freeze.setText("❄ FROZEN")
            self._log("❄ 프레임 고정 — 현재 프레임 유지")
        else:
            self._act_freeze.setText("❄ FREEZE")
            self._log("▶ 프레임 재개")

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
            self._log("ROI 초기화")
        else:
            super().keyPressEvent(event)

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

    # ── #14 라이브 SPE 저장 ──────────────────────────────────────────

    def _save_live_spe(self):
        # 뷰어에 실제 표시된 프레임 사용 — 카메라 최신 프레임(last_raw)이 아님
        raw = self._viewer_raw
        if raw is None:
            self._log("⚠️ 저장할 프레임 없음 (뷰어에 이미지 없음)"); return
        save_dir = "Live_Captures"
        os.makedirs(save_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        cam_name = (type(self._cam).__name__.replace("Camera", "")
                    if self._cam else "Live")
        path = os.path.join(save_dir, f"live_{cam_name}_{ts}.spe")
        try:
            exp_ms = 0.0
            if self._cam is not None:
                try:
                    exp_ms = self._cam.get_exposure_ms()
                except Exception:
                    pass
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

        cx, cy, br, _ = self._last_centroid
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

    # ── #1 타임스탬프 #3 색상 구분 로그 ──────────────────────────────

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")

        # 색상 분기 (#3)
        if any(k in msg for k in ("✅", "💾", "▶", "📸")):
            color = "#4ecdc4"   # teal  — 성공/정상
        elif any(k in msg for k in ("⚠️",)):
            color = "#ffe66d"   # yellow — 경고
        elif any(k in msg for k in ("❌", "FAIL", "실패", "오류")):
            color = "#e94560"   # red   — 에러
        elif any(k in msg for k in ("❄",)):
            color = "#a0c8ff"   # blue  — freeze
        elif any(k in msg for k in ("■", "연결 해제")):
            color = "#4a5a7a"   # dim   — 정지/해제
        elif "🔄" in msg:
            color = "#ffe66d"   # yellow — 진행 중
        else:
            color = "#00cc88"   # default green

        ts_html  = f"<span style='color:#2a4060;font-size:10px'>[{ts}]</span>"
        msg_html = f"<span style='color:{color}'>{msg}</span>"
        self.log_display.append(f"{ts_html} {msg_html}")

    # ── 정리 ─────────────────────────────────────────────────────────

    def cleanup(self):
        self._centroid_timer.stop()
        QSettings("SpeAnalyze", "LiveTab").setValue("dockState", self.saveState())
        # 앱 종료 시 — 비동기 워커를 기다리지 않고 직접 동기 정리
        if self._cam:
            try: self._cam.stop_live()
            except Exception: pass
            try: self._cam.disconnect()
            except Exception: pass
            self._cam = None
        self._proc_worker.stop()
        for t in (self._conn_thread, self._disc_thread):
            if t and t.isRunning():
                t.quit()
                t.wait(2000)
        self.motor_panel.cleanup()
