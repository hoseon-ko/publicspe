"""DeepAlign 구성 루트 파일.

이 파일은 DeepAlign 탭의 최상위 위젯과 탭 전체 공통 상태를 관리합니다.
주요 역할은 다음과 같습니다.
- 내부 5개 페이지 스택 생성
- mirror/focus/align 페이지에서 재사용하는 패널 인스턴스 생성
- layout, frame pipeline, styles, camera controller mixin 결합
- MainWindow 및 다른 탭에서 호출하는 공개 bind/set API 제공
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QSplitter,
    QFileDialog, QListWidgetItem, QMessageBox,
    QFrame, QLabel, QProgressBar, QPushButton,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QSize, QEvent, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from core.config import get_config
from PyQt6.QtGui import QIcon, QPixmap, QImage, QPainter, QColor
from typing import Optional
import numpy as np

from ui.file_list_panel import SpeFileItem
from core.async_worker import SpeLoadWorker

from ui.deepalign.autofocus_panel import AutoFocusPanel
from ui.deepalign.wrappers import DeepAlignAcsPanel, DeepAlignMirrorPanel
from ui.motion.motion_tab import MotionTab
from ui.deepalign.deepalign_camera_controller import CameraControllerMixin
from ui.deepalign.deepalign_frame_pipeline import FramePipelineMixin
from ui.deepalign.deepalign_layout import LayoutBuilderMixin
from ui.deepalign.deepalign_styles import DeepAlignStylesMixin
from ui.deepalign.euv_laser_controller import EuvLaserController
from ui.deepalign.deepalign_workers import (
    _AcquireWorker, _LiveWorker, _BgCaptureWorker,
)
from core.workers import SnapWorker
from ui.deepalign.scan import (
    MirrorMover, KimmMover, AcsMover,
    _MirrorScanWorker, _KimmScanWorker, _AcsScanWorker,
)
from ui.deepalign.scan.scan_widgets import (
    MirrorScanWidget, KimmScanWidget, AcsScanWidget,
)
from ui.deepalign.scan.scan_analysis import (
    mirror_centroid_process_fn, kimm_sharpness_process_fn, make_thumbnail_rgb,
)
from ui.autofocus.af_worker import AutoFocusWorker
from theme.styles import C_BG_DARK, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD
from core.logger import dev_logger
from core.session.session_state import CameraConnectionState
from core.session.ownership import OWNER_DEEPALIGN
from core.spe_writer import save_spe


# ─────────────────────────────────────────────────────────────────────────────
# AutoFocusWorker용 thin proxy: hub API → worker 인터페이스 변환
# ─────────────────────────────────────────────────────────────────────────────

class _BgProgressOverlay(QWidget):
    """배경 캡처 중 전체 화면을 어둡게 덮는 모달 오버레이."""

    cancel_requested = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.hide()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        parent.installEventFilter(self)

        # ── 중앙 카드 ──────────────────────────────────────────────
        self._card = QFrame(self)
        self._card.setFixedWidth(320)
        self._card.setStyleSheet(f"""
            QFrame {{
                background: {C_BG_DARK};
                border: 2px solid #a855f7;
                border-radius: 14px;
            }}
        """)

        cl = QVBoxLayout(self._card)
        cl.setSpacing(16)
        cl.setContentsMargins(28, 28, 28, 28)

        _no_border = "background: transparent; border: none;"

        title = QLabel("CAPTURING BACKGROUND")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: #a855f7; font-size: 11px; font-weight: 900;"
                            f" letter-spacing: 3px; {_no_border}")
        cl.addWidget(title)

        self.lbl_frames = QLabel("0 / 0")
        self.lbl_frames.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_frames.setStyleSheet(f"color: {C_TEXT}; font-size: 32px; font-weight: 700; {_no_border}")
        cl.addWidget(self.lbl_frames)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet("""
            QProgressBar           { background: #1e293b; border-radius: 4px; border: none; }
            QProgressBar::chunk   { background: #a855f7; border-radius: 4px; }
        """)
        cl.addWidget(self.progress_bar)

        self.lbl_eta = QLabel("Initializing...")
        self.lbl_eta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_eta.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 12px; font-weight: 600; {_no_border}")
        cl.addWidget(self.lbl_eta)

        btn_cancel = QPushButton("CANCEL")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.cancel_requested)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background: transparent; color: #ef4444;
                border: 1px solid #ef4444; border-radius: 6px;
                font-weight: 700; font-size: 12px; padding: 9px;
            }
            QPushButton:hover { background: rgba(239,68,68,0.15); }
            QPushButton:pressed { background: rgba(239,68,68,0.30); }
        """)
        cl.addWidget(btn_cancel)

        self._start_time: float = 0.0

    # ── public API ────────────────────────────────────────────────────

    def start(self, total: int) -> None:
        self._start_time = time.monotonic()
        self.lbl_frames.setText(f"0 / {total}")
        self.lbl_eta.setText("Initializing...")
        self.progress_bar.setValue(0)
        self._fit_to_parent()
        self._center_card()
        self.show()
        self.raise_()

    def update_progress(self, cur: int, total: int) -> None:
        self.lbl_frames.setText(f"{cur} / {total}")
        self.progress_bar.setValue(int(cur / max(1, total) * 100))
        if cur > 0:
            elapsed = time.monotonic() - self._start_time
            remaining = (total - cur) * (elapsed / cur)
            self.lbl_eta.setText(f"Est. remaining:  {remaining:.1f} s")

    # ── internals ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 185))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._center_card()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self._fit_to_parent()
        return super().eventFilter(obj, event)

    def _fit_to_parent(self) -> None:
        if self.parent():
            self.setGeometry(self.parent().rect())

    def _center_card(self) -> None:
        self._card.adjustSize()
        cw, ch = self._card.width(), self._card.height()
        self._card.move((self.width() - cw) // 2, (self.height() - ch) // 2)


class _HubCameraProxy:
    """hub.snap(owner) 또는 fallback camera.snap()을 worker 인터페이스로 노출."""
    def __init__(self, hub, fallback_camera):
        self._hub = hub
        self._cam = fallback_camera

    def snap(self):
        if self._hub is not None:
            return self._hub.snap(OWNER_DEEPALIGN)
        if self._cam is not None:
            return self._cam.snap()
        raise RuntimeError("카메라 없음")

    def is_valid(self) -> bool:
        return self._hub is not None or self._cam is not None


class _HubKimmProxy:
    """hub.kimm_move_to_z(z) 또는 fallback kimm_ctrl.move_to_z(z)를 worker 인터페이스로 노출."""
    def __init__(self, hub, fallback_kimm):
        self._hub = hub
        self._kimm = fallback_kimm

    def move_to_z(self, z: float) -> bool:
        if self._hub is not None:
            self._hub.kimm_move_to_z(z)
            return True
        if self._kimm is not None:
            return bool(self._kimm.move_to_z(z))
        return False

    def is_valid(self) -> bool:
        if self._hub is not None:
            try:
                from core.session.session_state import DeviceConnectionState
                state = self._hub.state
                return getattr(getattr(state, "motion", None), "kimm_connection", None) \
                       == DeviceConnectionState.CONNECTED
            except Exception:
                return False
        return self._kimm is not None and getattr(self._kimm, "is_connected", False)


# ─────────────────────────────────────────────────────────────────────────────
# Acquire 진행 상태를 하나의 객체로 관리 (산란 방지)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AcquireState:
    """Acquire 작업 전체 진행 상태. _begin_acquire_if_ready()에서 start()로 초기화."""
    running: bool = False
    cur: int = 0
    total: int = 0
    started_at: float = 0.0
    frame_expected_s: float = 0.0
    avg_frame_s: float = 0.0
    frame_started_at: float = 0.0
    frame_idx: int = 0
    stop_requested: bool = False

    def start(self, frame_count: int, expected_s: float) -> None:
        """Acquire 시작 시 상태 초기화."""
        t = time.monotonic()
        self.running = True
        self.cur = 0
        self.total = frame_count
        self.stop_requested = False
        self.frame_expected_s = expected_s
        self.avg_frame_s = 0.0
        self.started_at = t
        self.frame_started_at = t
        self.frame_idx = 1

    def reset(self) -> None:
        """Acquire 종료 후 상태 초기화."""
        self.running = False
        self.stop_requested = False


class DeepAlignMainTab(LayoutBuilderMixin, FramePipelineMixin, DeepAlignStylesMixin, CameraControllerMixin, QWidget):
    spe_saved = pyqtSignal(str)

    """
    DeepAlign Industrial Dashboard
    - 5-탭 아이콘 사이드바
    - 각 탭은 기존 완성된 패널을 ScrollArea로 감싸서 직접 임베드
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("deepAlignTab")
        self._camera = None
        self._live_tab: Optional[object] = None
        self._session_hub: Optional[object] = None
        self._scanned_devices: list[object] = []
        self._viewer_first_frame = True

        # 워커 스레드
        self._acq_thread: Optional[QThread] = None
        self._acq_worker: Optional[_AcquireWorker] = None
        self._snap_thread: Optional[QThread] = None
        self._snap_worker: Optional[SnapWorker] = None
        self._live_worker_thread: Optional[QThread] = None
        self._live_worker: Optional[_LiveWorker] = None
        self._af_worker: Optional[AutoFocusWorker] = None

        # SCAN workers (3 hardware, mutually exclusive — only one runs at a time)
        self._scan_thread: Optional[QThread] = None
        self._scan_worker: Optional[object] = None
        self._scan_owner_panel: Optional[object] = None
        self._scan_acs_mover: Optional[AcsMover] = None

        # Acquire 상태 (dataclass로 통합 관리)
        self._acq = AcquireState()

        # Image processing
        self._proc_image: np.ndarray | None = None
        self._proc_mode: int = 1
        self._proc_enabled: bool = False
        self._proc_region: str = "full"   # "full" | "roi"
        self._proc_bg_mode: str = "ring"  # "ring" | "manual" | "none"
        # proc ROI는 interaction_layer의 전용 아이템으로 관리 (roi_id 불필요)

        # Background subtraction
        self._bg_frame: np.ndarray | None = None
        self._bg_enabled: bool = False
        self._bg_save_folder: str = ""
        self._bg_capture_thread: QThread | None = None
        self._bg_capture_worker: _BgCaptureWorker | None = None
        self._bg_overlay: _BgProgressOverlay | None = None  # init_ui 이후 생성

        # Live 진행 타이머
        self._hub_live_progress_timer = QTimer(self)
        self._hub_live_progress_timer.setInterval(20)
        self._hub_live_progress_timer.timeout.connect(self._on_hub_live_progress_tick)
        self._hub_live_progress_started_at = 0.0
        self._hub_live_progress_cycle_s = 0.05

        # Snap 진행 타이머
        self._snap_progress_timer = QTimer(self)
        self._snap_progress_timer.setInterval(20)
        self._snap_progress_timer.timeout.connect(self._on_snap_progress_tick)
        self._snap_started_at = 0.0
        self._snap_expected_s = 0.05
        self._snap_in_progress = False

        # Acquire 진행률 타이머 (부드러운 애니메이션)
        self._acq_progress_timer = QTimer(self)
        self._acq_progress_timer.setInterval(20)
        self._acq_progress_timer.timeout.connect(self._on_acquire_progress_tick)

        # ── 카드 wrapper 인스턴스 (PicoCard/AcsCard 재활용) ───────────
        # Mirror/ACS는 ui/widgets/의 카드를 composition으로 wrap. AutoFocus는 원래
        # KimmZCard를 내부에서 사용 중이므로 그대로 유지.
        self.mirror_panel = DeepAlignMirrorPanel()
        self.af_panel     = AutoFocusPanel()
        self.align_panel  = DeepAlignAcsPanel()
        self.motion_panel = MotionTab()

        # ── 스캔 위젯(장치 패널과 분리, 워크플로우 전용) ─────────────
        self.mirror_scan = MirrorScanWidget()
        self.kimm_scan   = KimmScanWidget()
        self.acs_scan    = AcsScanWidget()
        self._cfg = get_config()

        self._init_ui()
        self._bg_overlay = _BgProgressOverlay(self)
        self._init_frame_convert_worker()
        self._init_laser_control()
        self._restore_settings()
        self._wire_camera_actions()
        self._apply_camera_capabilities(None)
        self._set_camera_action_state(False)
        self._apply_global_styles()

    # ─────────────────────────────────────────────────────────────────
    # UI 초기화
    # ─────────────────────────────────────────────────────────────────

    def _init_ui(self):
        main_lay = QHBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # 1. 아이콘 사이드바 (좌측)
        sidebar = self._create_icon_sidebar()
        main_lay.addWidget(sidebar)

        # 2. 중앙 패널 스택
        self.central_stack = QStackedWidget()
        self.central_stack.setObjectName("deepAlignStack")
        self.central_stack.setMinimumWidth(260)
        self.central_stack.setMaximumWidth(16_777_215)
        self.central_stack.setStyleSheet(
            "background-color: #0d121d; border-right: 1px solid #1e293b;"
        )

        # 3. 우측 작업영역: 도킹(QDockWidget) + 마스터바
        self.dock_host = self._create_docking_workspace()

        self.central_stack.addWidget(self._create_cam_page())              # 0
        self.central_stack.addWidget(self._wrap_panel(self.mirror_panel, extras=[self.mirror_scan]))  # 1
        self.central_stack.addWidget(self._wrap_panel(self.af_panel,     extras=[self.kimm_scan]))    # 2
        self.central_stack.addWidget(self._create_align_page())            # 3
        self.central_stack.addWidget(self._wrap_panel(self.motion_panel))  # 4
        self.central_stack.addWidget(self._create_analysis_page())         # 5

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter = right_splitter
        right_splitter.setChildrenCollapsible(False)
        right_splitter.addWidget(self.dock_host)
        self.master_bar = self._create_master_bar()
        right_splitter.addWidget(self.master_bar)
        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 0)
        right_splitter.setSizes([900, 95])

        # 4. 메인 그리드: 스플리터 필수 적용
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter = main_splitter
        main_splitter.setObjectName("deepAlignMainSplitter")
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setHandleWidth(6)
        main_splitter.setOpaqueResize(True)
        main_splitter.addWidget(self.central_stack)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([450, 1240])
        main_lay.addWidget(main_splitter, 1)

        # 시그널 연결
        self.mirror_panel.log_message.connect(self._on_sub_panel_log)
        self.align_panel.log_message.connect(self._on_sub_panel_log)
        self.motion_panel.log_message.connect(self._on_sub_panel_log)

    def _wire_camera_actions(self):
        # ── Camera page controls ──────────────────────────────────────
        self.btn_scan.clicked.connect(self._on_scan_clicked)
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_disconnect.clicked.connect(self._on_disconnect_clicked)
        self.btn_apply_exp.clicked.connect(self._on_apply_exposure_clicked)
        self.btn_apply_fps.clicked.connect(self._on_apply_fps_clicked)
        self.btn_apply_temp.clicked.connect(self._on_apply_temp_clicked)
        self.btn_apply_adc.clicked.connect(self._on_apply_adc_clicked)

        # ── Master bar — Camera 탭 ────────────────────────────────────
        self.btn_snap.clicked.connect(self._on_snap_clicked)
        self.btn_live_air.clicked.connect(self._on_start_live_clicked)
        self.btn_acquire.clicked.connect(self._on_acquire_clicked)
        self.btn_stop_main.clicked.connect(self._on_stop_live_clicked)
        self.cam_viewer.roi_added.connect(self._on_roi_added_from_viewer)
        self.cam_viewer.roi_selected.connect(self._on_roi_item_clicked_from_viewer)

        # ── Global Analysis Panels Connection ────────────────────────
        self.cam_viewer.profile_updated.connect(
            lambda data, lbl: self.plot_panel.plot_line(data, lbl)
        )
        self.cam_viewer.multi_profile_updated.connect(
            lambda d1, d2: self.plot_panel.plot_two_lines(d1, d2, "X mean", "Y mean")
        )
        self.cam_viewer.histogram_updated.connect(self.hist_panel.plot_histogram)

        # Viewer UI Toggle Requests
        self.cam_viewer.viewer.toggle_analysis_requested.connect(self._on_toggle_analysis_requested)
        self.cam_viewer.viewer.save_spe_requested.connect(self._on_save_current_spe)

        # ── Master bar — Mirror 탭 ────────────────────────────────────
        self.btn_mirror_zero_all.clicked.connect(self.mirror_panel.zero_all)
        self.btn_mirror_reset.clicked.connect(self.mirror_panel.reset_controller)
        self.btn_mirror_stop.clicked.connect(self.mirror_panel.stop_all)

        # ── Master bar — AutoFocus 탭 ─────────────────────────────────
        # KimmScanWidget 가 스캔 트리거를 담당하므로 AF 전용 RUN/ABORT/SET-Z
        # 버튼은 숨김. AutoFocusPanel 의 stub 메서드는 deprecated 로 남음.
        for _b in (self.btn_af_run, self.btn_af_abort, self.btn_af_set_z):
            _b.setVisible(False)

        # ── SCAN — 3 hardware (mirror/af/acs), 위젯은 패널과 분리됨 ──
        self.mirror_scan.scan_requested.connect(self._on_mirror_scan_requested)
        self.mirror_scan.scan_stop_requested.connect(self._on_scan_stop_requested)
        self.mirror_scan.save_last_requested.connect(
            lambda: self._save_scan_spe_dialog(self.mirror_scan))
        self.kimm_scan.scan_requested.connect(self._on_kimm_scan_requested)
        self.kimm_scan.scan_stop_requested.connect(self._on_scan_stop_requested)
        self.kimm_scan.save_last_requested.connect(
            lambda: self._save_scan_spe_dialog(self.kimm_scan))
        self.kimm_scan.servo_on_requested.connect(self._on_kimm_servo_on_requested)
        self.kimm_scan.servo_off_requested.connect(self._on_kimm_servo_off_requested)
        self.acs_scan.scan_requested.connect(self._on_acs_scan_requested)
        self.acs_scan.scan_stop_requested.connect(self._on_scan_stop_requested)
        self.acs_scan.save_last_requested.connect(
            lambda: self._save_scan_spe_dialog(self.acs_scan))
        # baseline DOF 동기화: AcsScanWidget의 SYNC 버튼 → align_panel의 현재 DOF값
        self.acs_scan.set_baseline_provider(self.align_panel.get_baseline_dof)
        # current pos provider: MirrorScanWidget → session_hub 경유 Picomotor 위치 조회
        def _pico_pos(motor: int):
            hub = self._session_hub
            if hub is None or not hub.is_pico_connected():
                return None
            try:
                return int(hub.pico_get_position(int(motor)))
            except Exception:
                return None
        self.mirror_scan.set_current_pos_provider(_pico_pos)

        # ── Master bar — Align 탭 ─────────────────────────────────────
        self.btn_align_enable.clicked.connect(self.align_panel.enable_all)
        self.btn_align_calc.clicked.connect(self._on_calc_kinem_clicked)
        self.btn_kinem_config_browse.clicked.connect(self._on_kinem_config_browse)
        self.btn_align_move.clicked.connect(self.align_panel.run)
        self.btn_align_stop.clicked.connect(self.align_panel.stop_all)

        # ── Master bar — Motion 탭 ────────────────────────────────────
        self.btn_motion_refresh.clicked.connect(self.motion_panel.refresh_positions)
        self.btn_motion_reconnect.clicked.connect(self.motion_panel.reconnect_all_devices)
        self.btn_motion_stop.clicked.connect(self.motion_panel.stop_all_motion)

        # ── Master bar — Analysis 탭 ──────────────────────────────────
        self.btn_an_open.clicked.connect(self._on_an_open_clicked)
        self.btn_an_roi_range.clicked.connect(self._on_an_roi_range_clicked)
        self.btn_an_fit.clicked.connect(self._on_an_fit_clicked)
        self.btn_toggle_plot_sm.toggled.connect(self.dock_plot.setVisible)
        self.btn_toggle_hist_sm.toggled.connect(self.dock_hist.setVisible)
        self.btn_toggle_proc_sm.toggled.connect(self.dock_proc_stats.setVisible)
        self.btn_toggle_roi_sm.toggled.connect(self.dock_roi.setVisible)
        self.btn_toggle_table_sm.toggled.connect(self.dock_proc_table.setVisible)
        self.dock_plot.visibilityChanged.connect(
            lambda visible: self._sync_analysis_dock_toggle(self.dock_plot, self.btn_toggle_plot_sm, visible)
        )
        self.dock_hist.visibilityChanged.connect(
            lambda visible: self._sync_analysis_dock_toggle(self.dock_hist, self.btn_toggle_hist_sm, visible)
        )
        self.dock_proc_stats.visibilityChanged.connect(
            lambda visible: self._sync_analysis_dock_toggle(self.dock_proc_stats, self.btn_toggle_proc_sm, visible)
        )
        self.dock_roi.visibilityChanged.connect(
            lambda visible: self._sync_analysis_dock_toggle(self.dock_roi, self.btn_toggle_roi_sm, visible)
        )
        self.dock_proc_table.visibilityChanged.connect(
            lambda visible: self._sync_analysis_dock_toggle(self.dock_proc_table, self.btn_toggle_table_sm, visible)
        )
        self.btn_reset_dock.clicked.connect(self._on_reset_dock_layout)

        # ── Analysis Toolbar Connections ──────────────────────────────
        self.act_an_open.triggered.connect(self._on_an_open_clicked)
        self.act_an_roi_range.triggered.connect(self._on_an_roi_range_toggled)
        self.act_an_fit.triggered.connect(self._on_an_fit_clicked)

        # File List Panel (Global)
        self.file_list_panel.file_selected.connect(self._on_file_selected)
        self.file_list_panel.frame_changed.connect(self._on_frame_changed)
        self.file_list_panel.file_removed.connect(self.frame_grid_panel.remove_file)

        self.frame_grid_panel.frame_clicked.connect(self._on_grid_frame_clicked)
        self.frame_grid_panel.checked_frames_changed.connect(self._on_checked_frames_changed)

        self._sync_analysis_dock_toggle(self.dock_plot, self.btn_toggle_plot_sm, self.dock_plot.isVisible())
        self._sync_analysis_dock_toggle(self.dock_hist, self.btn_toggle_hist_sm, self.dock_hist.isVisible())
        self._sync_analysis_dock_toggle(self.dock_proc_stats, self.btn_toggle_proc_sm, self.dock_proc_stats.isVisible())
        self._sync_analysis_dock_toggle(self.dock_roi, self.btn_toggle_roi_sm, self.dock_roi.isVisible())
        self._sync_analysis_dock_toggle(self.dock_proc_table, self.btn_toggle_table_sm, self.dock_proc_table.isVisible())

        # ROI Panel
        self.roi_panel.roi_selected.connect(self._on_roi_selected)
        self.roi_panel.roi_deleted.connect(self._on_roi_deleted)
        self.roi_panel.roi_goto.connect(self._on_roi_goto)

        # ── Settings persistence ──────────────────────────────────────
        self.cb_vendor.currentTextChanged.connect(self._on_vendor_changed)
        self.spin_exposure.valueChanged.connect(self._save_settings)
        self.spin_fps.valueChanged.connect(self._save_settings)
        self.check_fps_lock.toggled.connect(self._save_settings)
        self.spin_temp.valueChanged.connect(self._save_settings)
        self.chk_laser_temp_alarm.toggled.connect(self._save_settings)
        self.spin_laser_temp_alarm_min.valueChanged.connect(self._save_settings)
        self.cb_adc_quality.currentTextChanged.connect(self._save_settings)
        self.cb_adc_speed.currentTextChanged.connect(self._save_settings)
        self.cb_adc_gain.currentTextChanged.connect(self._save_settings)
        self.cb_adc_bit.currentTextChanged.connect(self._save_settings)
        self.spin_frame_to_save.valueChanged.connect(self._save_settings)
        self.edit_folder.textChanged.connect(self._save_settings)
        self.edit_file_base.textChanged.connect(self._save_settings)
        self.check_inc_name.toggled.connect(self._save_settings)
        self.check_add_date.toggled.connect(self._save_settings)
        self.check_add_time.toggled.connect(self._save_settings)
        self.cb_date_fmt.currentTextChanged.connect(self._save_settings)
        self.cb_time_fmt.currentTextChanged.connect(self._save_settings)
        self.cb_place.currentTextChanged.connect(self._save_settings)

        # ── Image processing ─────────────────────────────────────────
        self.check_use_proc.toggled.connect(self._on_proc_enable_toggled)
        self._proc_mode_group.idClicked.connect(self._on_proc_mode_changed)
        self._proc_region_group.idClicked.connect(self._on_proc_region_changed)
        self.btn_proc_load.clicked.connect(self._on_proc_load_clicked)
        self.btn_draw_sig_roi.clicked.connect(self._start_draw_sig_roi)
        self.btn_auto_refine_roi.clicked.connect(self._auto_refine_roi)
        self.btn_draw_bg_roi.clicked.connect(self._start_draw_bg_roi)
        self.btn_clear_roi.clicked.connect(self._clear_proc_rois)
        self._proc_bg_group.idClicked.connect(self._on_proc_bg_mode_changed)
        self.spin_bg_gap.valueChanged.connect(self._save_settings)
        self.spin_bg_gap.valueChanged.connect(self._refresh_ring_overlay)
        self.spin_bg_thickness.valueChanged.connect(self._save_settings)
        self.spin_bg_thickness.valueChanged.connect(self._refresh_ring_overlay)
        self.spin_refine_threshold.valueChanged.connect(self._save_settings)
        self.spin_refine_blur.valueChanged.connect(self._save_settings)
        self.spin_refine_margin.valueChanged.connect(self._save_settings)
        self.spin_refine_expand.valueChanged.connect(self._save_settings)

        # ── Ring BG 오버레이 초기화 ───────────────────────────────────
        self._init_ring_overlay()

        # ── Background subtraction ────────────────────────────────────
        self.check_use_bg.toggled.connect(self._on_bg_enable_toggled)
        self.btn_bg_capture.clicked.connect(self._on_bg_capture_clicked)
        self.btn_bg_load.clicked.connect(self._on_bg_load_clicked)
        self.btn_bg_browse.clicked.connect(self._on_bg_browse_clicked)
        self.btn_bg_clear.clicked.connect(self._on_bg_clear_clicked)

    def bind_live_tab(self, live_tab=None):
        """LiveTab 연동 진입점 — LiveTab 제거 후 호환성 유지용. 실제 동작 없음."""
        self._live_tab = None  # DeepAlign은 SessionHub로 독립 운영

    def bind_session_hub(self, session_hub):
        self._session_hub = session_hub
        if self._session_hub is None:
            self._set_camera_action_state(False)
            return
        self.mirror_panel.bind_session_hub(session_hub)
        self.motion_panel.bind_session_hub(session_hub)
        self.align_panel.bind_session_hub(session_hub)
        self.af_panel.bind_session_hub(session_hub)
        try:
            self._session_hub.select_camera_vendor(self._vendor_key())
        except Exception:
            pass
        try:
            state = self._session_hub.get_camera_state()
            connected = getattr(state, "connection", None) == CameraConnectionState.CONNECTED
        except Exception:
            connected = False
        self._set_camera_action_state(bool(connected))

    def _on_live_progress_changed(self, value: int):
        self._set_master_progress(value)

    # ─────────────────────────────────────────────────────────────────
    # Image processing
    # ─────────────────────────────────────────────────────────────────

    def _on_proc_enable_toggled(self, checked: bool) -> None:
        self._proc_enabled = checked
        has_img = self._proc_image is not None
        # Mode 1/2 는 proc image 필요. Mode 3 는 raw 자체 분석이라 무조건 enable.
        self.radio_proc_mode1.setEnabled(checked and has_img)
        self.radio_proc_mode2.setEnabled(checked and has_img)
        self.radio_proc_mode3.setEnabled(checked)
        # Mode 1/2 가 disabled 인데 현재 선택되어 있으면 Mode 3 으로 자동 전환
        if checked and not has_img and self._proc_mode in (1, 2):
            self.radio_proc_mode3.setChecked(True)
            self._proc_mode = 3
        # Region radio 도 enable 따라감
        self.radio_region_full.setEnabled(checked)
        self.radio_region_roi.setEnabled(checked)
        # Signal ROI 버튼은 proc enabled 이면 항상 활성
        self.btn_draw_sig_roi.setEnabled(checked)
        # ROI 초기화 버튼도 proc enabled 이면 활성
        self.btn_clear_roi.setEnabled(checked)
        # Auto-Refine 파라미터 spinbox: proc enabled 이면 항상 활성
        for sp in ('spin_refine_threshold', 'spin_refine_blur',
                   'spin_refine_margin', 'spin_refine_expand'):
            w = getattr(self, sp, None)
            if w: w.setEnabled(checked)
        # 나머지는 region=="roi" 일 때만 활성
        roi_mode = checked and self._proc_region == "roi"
        # AUTO-REFINE: proc ROI가 존재하는지 interaction_layer에서 직접 확인
        has_sig_roi = False
        try:
            has_sig_roi = self.cam_viewer.view.interactions.get_proc_roi('signal') is not None
        except AttributeError:
            pass
        self.btn_auto_refine_roi.setEnabled(roi_mode and has_sig_roi)
        self.radio_bg_ring.setEnabled(roi_mode)
        self.radio_bg_manual.setEnabled(roi_mode)
        self.radio_bg_none.setEnabled(roi_mode)
        ring_params   = roi_mode and self._proc_bg_mode == "ring"
        manual_active = roi_mode and self._proc_bg_mode == "manual"
        self.spin_bg_gap.setEnabled(ring_params)
        self.spin_bg_thickness.setEnabled(ring_params)
        self.btn_draw_bg_roi.setEnabled(manual_active)
        self._refresh_ring_overlay()

    def _on_proc_mode_changed(self, mode_id: int) -> None:
        self._proc_mode = mode_id

    def _on_proc_region_changed(self, region_id: int) -> None:
        self._proc_region = "roi" if region_id == 1 else "full"
        roi_mode = self._proc_enabled and self._proc_region == "roi"
        # Auto-Refine 파라미터 spinbox: proc enabled 이면 항상 활성 (region 무관)
        for sp in ('spin_refine_threshold', 'spin_refine_blur',
                   'spin_refine_margin', 'spin_refine_expand'):
            w = getattr(self, sp, None)
            if w: w.setEnabled(self._proc_enabled)
        has_sig_roi = False
        try:
            has_sig_roi = self.cam_viewer.view.interactions.get_proc_roi('signal') is not None
        except AttributeError:
            pass
        self.btn_auto_refine_roi.setEnabled(roi_mode and has_sig_roi)
        self.radio_bg_ring.setEnabled(roi_mode)
        self.radio_bg_manual.setEnabled(roi_mode)
        self.radio_bg_none.setEnabled(roi_mode)
        ring_params   = roi_mode and self._proc_bg_mode == "ring"
        manual_active = roi_mode and self._proc_bg_mode == "manual"
        self.spin_bg_gap.setEnabled(ring_params)
        self.spin_bg_thickness.setEnabled(ring_params)
        self.btn_draw_bg_roi.setEnabled(manual_active)
        self._refresh_ring_overlay()
        self._save_settings()

    def _on_proc_bg_mode_changed(self, mode_id: int) -> None:
        modes = {0: "ring", 1: "manual", 2: "none"}
        self._proc_bg_mode = modes.get(mode_id, "ring")
        roi_mode      = self._proc_enabled and self._proc_region == "roi"
        ring_params   = roi_mode and self._proc_bg_mode == "ring"
        manual_active = roi_mode and self._proc_bg_mode == "manual"
        self.spin_bg_gap.setEnabled(ring_params)
        self.spin_bg_thickness.setEnabled(ring_params)
        self.btn_draw_bg_roi.setEnabled(manual_active)
        self._refresh_ring_overlay()
        self._save_settings()

    def _start_draw_sig_roi(self) -> None:
        """Signal ROI 그리기 시작 — 전용 proc_signal 모드 (일반 ROI 시스템과 무관)."""
        try:
            self.cam_viewer.view.interactions.set_roi_mode("proc_signal")
        except AttributeError:
            pass

    def _start_draw_bg_roi(self) -> None:
        """Manual BG ROI 그리기 시작 — 전용 proc_bg 모드 (일반 ROI 시스템과 무관)."""
        try:
            self.cam_viewer.view.interactions.set_roi_mode("proc_bg")
        except AttributeError:
            pass

    def _clear_proc_rois(self) -> None:
        """Signal / BG proc ROI를 모두 지운다. clear_proc_roi가 proc_roi_updated(mode, None)을
        emit → _on_proc_roi_updated가 라벨/오버레이/auto-refine 버튼 상태를 동기화."""
        try:
            it = self.cam_viewer.view.interactions
            it.clear_proc_roi('signal')
            it.clear_proc_roi('bg')
            it.set_roi_mode(None)
        except AttributeError:
            pass

    def _on_proc_load_clicked(self) -> None:
        start = self.edit_folder.text().strip() or "."
        path, _ = QFileDialog.getOpenFileName(
            self, "처리 이미지 파일 선택", start,
            "All Supported (*.spe *.npy *.npz *.tif *.tiff *.png *.bmp);;"
            "SPE Files (*.spe);;"
            "NumPy (*.npy *.npz);;"
            "Images (*.tif *.tiff *.png *.bmp)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext == ".spe":
                from core.spe_reader import SpeFile
                data = SpeFile(path).data
                img = data[0] if data.ndim == 3 else data
            elif ext == ".npz":
                archive = np.load(path)
                img = archive[list(archive.keys())[0]]
            elif ext == ".npy":
                img = np.load(path)
            else:
                try:
                    import cv2
                    img = cv2.imread(path, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_GRAYSCALE)
                    if img is None:
                        raise ValueError("cv2 load failed")
                except ImportError:
                    from PIL import Image
                    img = np.array(Image.open(path))

            self._proc_image = np.squeeze(img)
            self._proc_update_ui(Path(path).name)
        except Exception as e:
            dev_logger.warning(f"[ProcImage] 로드 실패: {e}")
            self.lbl_proc_status.setText(f"Load failed: {e}")
            self.lbl_proc_status.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold;")

    def _proc_update_ui(self, filename: str = "") -> None:
        has_img = self._proc_image is not None
        # check_use_proc 는 항상 활성 (Mode 3 가 proc image 불요)
        self.check_use_proc.setEnabled(True)
        # Mode 1/2 는 proc image 있고 enable 상태일 때만
        en = self._proc_enabled
        self.radio_proc_mode1.setEnabled(en and has_img)
        self.radio_proc_mode2.setEnabled(en and has_img)
        self.radio_proc_mode3.setEnabled(en)
        # 이미지가 없는데 현재 모드가 1/2 면 3 으로 자동 전환
        if not has_img and self._proc_mode in (1, 2):
            self.radio_proc_mode3.setChecked(True)
            self._proc_mode = 3
        if not has_img:
            self.lbl_proc_status.setText("No image (Mode 3 만 가능)")
            self.lbl_proc_status.setStyleSheet(f"color: {C_TEXT_DIM}; font-size: 11px; font-weight: bold;")
            return
        shape = self._proc_image.shape
        dims  = f"{shape[1]}×{shape[0]}" if self._proc_image.ndim >= 2 else f"{shape[0]}"
        self.lbl_proc_status.setText(f"{filename}  ({dims}, {self._proc_image.dtype})")
        self.lbl_proc_status.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold;")

    # ─────────────────────────────────────────────────────────────────
    # Background subtraction
    # ─────────────────────────────────────────────────────────────────

    def _on_bg_enable_toggled(self, checked: bool) -> None:
        self._bg_enabled = checked

    def _on_bg_capture_clicked(self) -> None:
        if not self._is_hub_camera_connected():
            return
        if self._bg_capture_thread and self._bg_capture_thread.isRunning():
            return

        # Live 중이면 중단 후 캡처 (snap과 동일 패턴)
        self._was_live_before_bg = getattr(self, "_hub_live_active", False)
        if self._was_live_before_bg:
            self._stop_hub_live()

        n = self.spin_bg_frames.value()
        acquire_fn = lambda cb, stop: self._session_hub.acquire_with_progress(OWNER_DEEPALIGN, n, progress_cb=cb, should_stop=stop)

        self.btn_bg_capture.setEnabled(False)
        self.btn_bg_load.setEnabled(False)
        self.lbl_bg_status.setText(f"Capturing 0 / {n} frames...")
        self.lbl_bg_status.setStyleSheet("color: #facc15; font-size: 11px; font-weight: bold;")

        self._bg_capture_thread = QThread(self)
        self._bg_capture_worker = _BgCaptureWorker(acquire_fn, n)
        self._bg_capture_worker.moveToThread(self._bg_capture_thread)
        self._bg_capture_thread.started.connect(self._bg_capture_worker.run)
        self._bg_capture_worker.progress.connect(self._on_bg_capture_progress)
        self._bg_capture_worker.finished.connect(self._on_bg_capture_finished)
        self._bg_capture_worker.error.connect(self._on_bg_capture_error)
        self._bg_capture_worker.finished.connect(lambda _: self._bg_capture_thread.quit())
        self._bg_capture_worker.error.connect(lambda _: self._bg_capture_thread.quit())
        self._bg_capture_thread.finished.connect(self._cleanup_bg_capture_thread)
        self._bg_overlay.cancel_requested.connect(self._on_bg_capture_cancel)
        self._bg_overlay.start(n)
        self._bg_capture_thread.start()

    def _on_bg_capture_progress(self, cur: int, total: int) -> None:
        self._bg_overlay.update_progress(cur, total)

    def _on_bg_capture_cancel(self) -> None:
        if self._bg_capture_worker:
            self._bg_capture_worker.stop()
        # thread.quit()는 _cleanup에서 처리

    def _on_bg_capture_finished(self, averaged: np.ndarray) -> None:
        self._bg_frame = averaged
        # 자동 저장
        folder = self._bg_save_folder or self.edit_folder.text().strip() or "."
        stem   = self.edit_bg_filename.text().strip() or "background"
        fpath  = str(Path(folder) / f"{stem}.spe")
        try:
            save_spe(fpath, [self._bg_frame])
            self._bg_update_ui(source_name=Path(fpath).name)
        except Exception as e:
            dev_logger.warning(f"[BG] 자동 저장 실패: {e}")
            self._bg_update_ui(source_name="Captured (save failed)")

    def _on_bg_capture_error(self, msg: str) -> None:
        dev_logger.warning(f"[BG] Capture 실패: {msg}")
        self._bg_update_ui()

    def _cleanup_bg_capture_thread(self) -> None:
        self._bg_overlay.hide()
        # cancel 시그널 중복 연결 방지
        try:
            self._bg_overlay.cancel_requested.disconnect(self._on_bg_capture_cancel)
        except Exception:
            pass
        self.btn_bg_capture.setEnabled(True)
        self.btn_bg_load.setEnabled(True)
        if getattr(self, "_was_live_before_bg", False):
            self._was_live_before_bg = False
            self._start_hub_live()
        self._bg_capture_thread = None
        self._bg_capture_worker = None

    def _on_bg_load_clicked(self) -> None:
        from core.spe_reader import SpeFile
        start_dir = self._bg_save_folder or self.edit_folder.text().strip() or "."
        path, _ = QFileDialog.getOpenFileName(
            self, "배경 SPE 파일 선택", start_dir, "SPE Files (*.spe)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not path:
            return
        try:
            frames = SpeFile(path).data
            self._bg_frame = frames[0] if frames.ndim == 3 else frames
            self._bg_update_ui(source_name=Path(path).name)
        except Exception as e:
            dev_logger.warning(f"[BG] SPE 로드 실패: {e}")

    def _on_bg_browse_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "배경 저장 폴더 선택",
            self._bg_save_folder or self.edit_folder.text().strip() or ".",
            QFileDialog.Option.DontUseNativeDialog
        )
        if folder:
            self._bg_save_folder = folder

    def _on_bg_clear_clicked(self) -> None:
        self._bg_frame = None
        self._bg_enabled = False
        self.check_use_bg.setChecked(False)
        self._bg_update_ui()

    def _bg_update_ui(self, source_name: str = "") -> None:
        has_bg = self._bg_frame is not None
        self.btn_bg_capture.setEnabled(self._is_hub_camera_connected())
        self.btn_bg_clear.setEnabled(has_bg)
        self.check_use_bg.setEnabled(has_bg)
        if has_bg:
            h, w = self._bg_frame.shape[:2]
            self.lbl_bg_status.setText(f"{source_name}  ({w}×{h}, {self._bg_frame.dtype})")
            self.lbl_bg_status.setStyleSheet("color: #c084fc; font-size: 11px; font-weight: bold;")
        else:
            self.lbl_bg_status.setText("No background set")
            self.lbl_bg_status.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 11px; font-weight: bold;")

    def _on_vendor_changed(self, new_vendor: str):
        """Vendor 콤보박스 전환 시 호출.

        주의: 이 핸들러는 _save_settings 를 호출하면 안 된다.
        이유: 콤보박스가 막 바뀐 시점의 spinbox/콤보 값들은 여전히 *이전* vendor 의
        값을 들고 있다. 그대로 _save_settings 가 돌면 `vendor=new_vendor` 키에
        이전 vendor 값이 덮어쓰여 vendor 격리가 무너진다.

        대신: 새 vendor 의 저장값을 UI 에 로드 (시그널 차단하여 _save_settings
        재트리거 방지) → last_used 만 갱신 → save.
        """
        new_vendor = (new_vendor or "").strip()
        if not new_vendor:
            return
        c = self._cfg

        # 새 vendor 의 저장값 읽기
        exposure = float(c.get_camera_setting("exposure_ms", 20.0,  vendor=new_vendor))
        fps      = float(c.get_camera_setting("fps",         30.0,  vendor=new_vendor))
        fps_lock = bool(c.get_camera_setting("fps_lock",     False, vendor=new_vendor))
        device_id = str(c.get("camera.last_used.device_id", "") or "")
        temp     = float(c.get_camera_setting("temp_c",     -70.0,  vendor=new_vendor, device_id=device_id))
        adc_qual = str(c.get_camera_setting("adc.quality", "", vendor=new_vendor))
        adc_spd  = str(c.get_camera_setting("adc.speed",   "", vendor=new_vendor))
        adc_gain = str(c.get_camera_setting("adc.gain",    "", vendor=new_vendor))
        adc_bit  = str(c.get_camera_setting("adc.bit",     "", vendor=new_vendor))

        # UI 반영 — 모두 시그널 차단 (각 위젯의 *Changed → _save_settings 막기)
        widgets = (self.spin_exposure, self.spin_fps, self.check_fps_lock,
                   self.spin_temp, self.cb_adc_quality, self.cb_adc_speed,
                   self.cb_adc_gain, self.cb_adc_bit)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.spin_exposure.setValue(exposure)
            self.spin_fps.setValue(fps)
            self.check_fps_lock.setChecked(fps_lock)
            self.spin_temp.setValue(temp)
            for cb, val in [
                (self.cb_adc_quality, adc_qual),
                (self.cb_adc_speed,   adc_spd),
                (self.cb_adc_gain,    adc_gain),
                (self.cb_adc_bit,     adc_bit),
            ]:
                if val:
                    idx = cb.findText(val)
                    if idx >= 0:
                        cb.setCurrentIndex(idx)
                else:
                    # 저장값 없으면 첫 항목으로 (혹은 -1 로 비우기)
                    pass
        finally:
            for w in widgets:
                w.blockSignals(False)

        # last_used 만 갱신 — vendor별 dict 는 건드리지 않음
        c.set_last_camera(new_vendor, device_id=getattr(self, "_active_device_id", ""))
        c.save()

    def _save_settings(self):
        if getattr(self, "_is_loading", False):
            return
        c = self._cfg
        # Camera — vendor 별 분리 저장. last_used 도 갱신.
        vendor = self.cb_vendor.currentText()
        c.set_last_camera(vendor, device_id=getattr(self, "_active_device_id", ""))
        c.set_camera_setting("exposure_ms", float(self.spin_exposure.value()), vendor=vendor)
        c.set_camera_setting("fps",         float(self.spin_fps.value()),      vendor=vendor)
        device_id = str(c.get("camera.last_used.device_id", "") or "")
        c.set_camera_setting("temp_c",      float(self.spin_temp.value()),     vendor=vendor, device_id=device_id)
        
        c.set("tabs.deepalign.laser_temp_alarm.enabled", self.chk_laser_temp_alarm.isChecked())
        c.set("tabs.deepalign.laser_temp_alarm.min", float(self.spin_laser_temp_alarm_min.value()))
        c.set_camera_setting("adc.quality", self.cb_adc_quality.currentText(), vendor=vendor)
        c.set_camera_setting("adc.speed",   self.cb_adc_speed.currentText(),   vendor=vendor)
        c.set_camera_setting("adc.gain",    self.cb_adc_gain.currentText(),    vendor=vendor)
        c.set_camera_setting("adc.bit",     self.cb_adc_bit.currentText(),     vendor=vendor)
        # Save (DeepAlign 전용: tabs.deepalign.save.*)
        c.set("tabs.deepalign.save.frame_to_save", int(self.spin_frame_to_save.value()))
        c.set("tabs.deepalign.save.folder",        self.edit_folder.text())
        c.set("tabs.deepalign.save.file_base",     self.edit_file_base.text())
        c.set("tabs.deepalign.save.inc_name",      bool(self.check_inc_name.isChecked()))
        c.set("tabs.deepalign.save.add_date",      bool(self.check_add_date.isChecked()))
        c.set("tabs.deepalign.save.add_time",      bool(self.check_add_time.isChecked()))
        c.set("tabs.deepalign.save.date_fmt",      self.cb_date_fmt.currentText())
        c.set("tabs.deepalign.save.time_fmt",      self.cb_time_fmt.currentText())
        c.set("tabs.deepalign.save.place",         self.cb_place.currentText())
        # Image Processing 상태
        c.set("tabs.deepalign.proc.enabled",  bool(self.check_use_proc.isChecked()))
        c.set("tabs.deepalign.proc.mode",     int(self._proc_mode))
        c.set("tabs.deepalign.proc.region",   str(self._proc_region))
        c.set("tabs.deepalign.proc.bg_mode",      str(self._proc_bg_mode))
        c.set("tabs.deepalign.proc.bg_gap",       int(self.spin_bg_gap.value()))
        c.set("tabs.deepalign.proc.bg_thickness", int(self.spin_bg_thickness.value()))
        c.set("tabs.deepalign.proc.refine_threshold", float(self.spin_refine_threshold.value()))
        c.set("tabs.deepalign.proc.refine_blur",      float(self.spin_refine_blur.value()))
        c.set("tabs.deepalign.proc.refine_margin",    int(self.spin_refine_margin.value()))
        c.set("tabs.deepalign.proc.refine_expand",    int(self.spin_refine_expand.value()))
        # DeepAlign 내부 dock 레이아웃 (Plot/Hist/Proc/ROI 위치+가시성)
        try:
            c.set("window.deepalign.dockState", self.dock_host.saveState())
        except Exception:
            pass
        # ProcStats 패널 상태
        ps = self.proc_stats_panel
        c.set("tabs.deepalign.proc_stats.enabled",   bool(ps.chk_enable.isChecked()))
        src_id = ps._grp.checkedId()
        c.set("tabs.deepalign.proc_stats.source",
              "snap" if src_id == 0 else "live" if src_id == 1 else "all")
        for key in [f"opt{i}" for i in range(1, 18)]:
            if key in ps._checks:
                c.set(f"tabs.deepalign.proc_stats.show_{key}", bool(ps._checks[key].isChecked()))

        # Laser HTTP 설정 저장
        c.set("tabs.deepalign.laser.ip", self.edit_laser_ip.text())
        c.set("tabs.deepalign.laser.port", self.edit_laser_port.text())
        c.set("tabs.deepalign.laser.auth_mode", self.combo_laser_auth_type.currentIndex())
        c.set("tabs.deepalign.laser.id", self.edit_laser_id.text())
        c.set("tabs.deepalign.laser.pw", self.edit_laser_pw.text())
        c.set("tabs.deepalign.laser.token", self.edit_laser_token.text())
        c.set("tabs.deepalign.laser.poll", bool(self.btn_laser_poll.isChecked()))

        c.save()
        # 디바이스 패널 강제 저장 (사용자가 IP/Port 만 바꾸고 connect 안 한 경우 대비)
        for p in (self.align_panel, self.af_panel, self.mirror_panel, self.motion_panel):
            fn = getattr(p, "_save_settings", None)
            if callable(fn):
                try: fn()
                except Exception: pass

    def _restore_settings(self):
        self._is_loading = True
        try:
            c = self._cfg
            # Camera — last_used vendor 기준으로 그 vendor 의 설정만 로드
            vendor    = str(c.get("camera.last_used.vendor", "Simulation"))
            device_id = str(c.get("camera.last_used.device_id", "") or "")
            exposure  = float(c.get_camera_setting("exposure_ms", 20.0,  vendor=vendor))
            fps       = float(c.get_camera_setting("fps",         30.0,  vendor=vendor))
            fps_lock  = bool(c.get_camera_setting("fps_lock",     False, vendor=vendor))
            temp      = float(c.get_camera_setting("temp_c",     -70.0,  vendor=vendor, device_id=device_id))
            
            laser_alarm_en = bool(c.get("tabs.deepalign.laser_temp_alarm.enabled", False))
            laser_alarm_min = float(c.get("tabs.deepalign.laser_temp_alarm.min", -70.0))
            self.chk_laser_temp_alarm.setChecked(laser_alarm_en)
            self.spin_laser_temp_alarm_min.setValue(laser_alarm_min)
            adc_qual  = str(c.get_camera_setting("adc.quality", "", vendor=vendor))
            adc_spd   = str(c.get_camera_setting("adc.speed",   "", vendor=vendor))
            adc_gain  = str(c.get_camera_setting("adc.gain",    "", vendor=vendor))
            adc_bit   = str(c.get_camera_setting("adc.bit",     "", vendor=vendor))
            # Save
            frame_to_save = int(c.get("tabs.deepalign.save.frame_to_save", 10))
            save_folder   = str(c.get("tabs.deepalign.save.folder",    "Live_Captures"))
            file_base     = str(c.get("tabs.deepalign.save.file_base", "Capture"))
            inc_name      = bool(c.get("tabs.deepalign.save.inc_name",  False))
            add_date      = bool(c.get("tabs.deepalign.save.add_date",  True))
            add_time      = bool(c.get("tabs.deepalign.save.add_time",  True))
            date_fmt      = str(c.get("tabs.deepalign.save.date_fmt", "YYYY-Month-DD"))
            time_fmt      = str(c.get("tabs.deepalign.save.time_fmt", "hh:mm:ss (24h)"))
            place         = str(c.get("tabs.deepalign.save.place",    "Suffix"))

            # Camera 복원
            idx = self.cb_vendor.findText(vendor)
            if idx >= 0:
                self.cb_vendor.setCurrentIndex(idx)
            self.spin_exposure.setValue(float(exposure))
            self.spin_fps.setValue(float(fps))
            self.check_fps_lock.setChecked(bool(fps_lock))
            self.spin_temp.setValue(float(temp))
            for cb, val in [
                (self.cb_adc_quality, adc_qual),
                (self.cb_adc_speed,   adc_spd),
                (self.cb_adc_gain,    adc_gain),
                (self.cb_adc_bit,     adc_bit),
            ]:
                if val:
                    idx = cb.findText(val)
                    if idx >= 0:
                        cb.setCurrentIndex(idx)

            # Save 복원
            self.spin_frame_to_save.setValue(int(frame_to_save))
            self.edit_folder.setText(save_folder)
            self.edit_file_base.setText(file_base)
            self.check_inc_name.setChecked(bool(inc_name))
            self.check_add_date.setChecked(bool(add_date))
            self.check_add_time.setChecked(bool(add_time))

            for cb, val in [
                (self.cb_date_fmt, date_fmt),
                (self.cb_time_fmt, time_fmt),
                (self.cb_place,    place),
            ]:
                idx = cb.findText(val)
                if idx >= 0:
                    cb.setCurrentIndex(idx)

            # Image Processing 복원
            proc_en     = bool(c.get("tabs.deepalign.proc.enabled", False))
            proc_mode   = int(c.get("tabs.deepalign.proc.mode", 1))
            proc_region = str(c.get("tabs.deepalign.proc.region", "full"))
            self._proc_mode = proc_mode if proc_mode in (1, 2, 3) else 1
            self._proc_region = proc_region if proc_region in ("full", "roi") else "full"
            if self._proc_mode == 1: self.radio_proc_mode1.setChecked(True)
            elif self._proc_mode == 2: self.radio_proc_mode2.setChecked(True)
            else: self.radio_proc_mode3.setChecked(True)
            if self._proc_region == "roi": self.radio_region_roi.setChecked(True)
            else: self.radio_region_full.setChecked(True)
            bg_mode = str(c.get("tabs.deepalign.proc.bg_mode", "ring"))
            self._proc_bg_mode = bg_mode if bg_mode in ("ring", "manual", "none") else "ring"
            {
                "ring":   self.radio_bg_ring,
                "manual": self.radio_bg_manual,
                "none":   self.radio_bg_none,
            }[self._proc_bg_mode].setChecked(True)
            self.spin_bg_gap.setValue(int(c.get("tabs.deepalign.proc.bg_gap", 2)))
            self.spin_bg_thickness.setValue(int(c.get("tabs.deepalign.proc.bg_thickness", 10)))
            self.spin_refine_threshold.setValue(float(c.get("tabs.deepalign.proc.refine_threshold", 50.0)))
            self.spin_refine_blur.setValue(float(c.get("tabs.deepalign.proc.refine_blur", 2.0)))
            self.spin_refine_margin.setValue(int(c.get("tabs.deepalign.proc.refine_margin", 5)))
            self.spin_refine_expand.setValue(int(c.get("tabs.deepalign.proc.refine_expand", 20)))

            self.check_use_proc.setChecked(proc_en)
            # _restore_settings 는 시그널 연결 이전에 호출되므로 핸들러 명시 호출
            # (enable 규칙 / radio enable / mode·region 변수 동기화)
            self._on_proc_enable_toggled(proc_en)
            self._on_proc_mode_changed(self._proc_mode)
            self._on_proc_region_changed(1 if self._proc_region == "roi" else 0)

            # DeepAlign 내부 dock 레이아웃 복원 (Plot/Hist/Proc/ROI 위치+가시성)
            dstate = c.get("window.deepalign.dockState")
            if dstate:
                try:
                    self.dock_host.restoreState(dstate)
                except Exception:
                    pass
            # 복원 후 viewer toolbar 버튼들 강제 동기화
            for dock, btn in (
                (self.dock_plot, self.btn_toggle_plot_sm),
                (self.dock_hist, self.btn_toggle_hist_sm),
                (self.dock_proc_stats, self.btn_toggle_proc_sm),
                (self.dock_roi, self.btn_toggle_roi_sm),
                (self.dock_proc_table, self.btn_toggle_table_sm),
            ):
                self._sync_analysis_dock_toggle(dock, btn, dock.isVisible())

            # ProcStats 복원
            ps = self.proc_stats_panel
            ps.chk_enable.setChecked(bool(c.get("tabs.deepalign.proc_stats.enabled", False)))
            src = str(c.get("tabs.deepalign.proc_stats.source", "snap"))
            if   src == "live": ps.radio_live.setChecked(True)
            elif src == "all":  ps.radio_all.setChecked(True)
            else:               ps.radio_snap.setChecked(True)
            for key in [f"opt{i}" for i in range(1, 18)]:
                if key in ps._checks:
                    default_val = key in ("opt1", "opt2", "opt3", "opt13")  # Mean/Median/RMS/SNR 기본 활성
                    ps._checks[key].setChecked(bool(c.get(f"tabs.deepalign.proc_stats.show_{key}", default_val)))

            self._update_save_control_state()
            self._update_save_preview()

            # Laser HTTP 복원
            laser_ip = str(c.get("tabs.deepalign.laser.ip", "127.0.0.1"))
            laser_port = str(c.get("tabs.deepalign.laser.port", "5643"))
            laser_auth_mode = int(c.get("tabs.deepalign.laser.auth_mode", 0))
            laser_id = str(c.get("tabs.deepalign.laser.id", "viewer"))
            laser_pw = str(c.get("tabs.deepalign.laser.pw", ""))
            laser_token = str(c.get("tabs.deepalign.laser.token", ""))
            laser_poll = bool(c.get("tabs.deepalign.laser.poll", False))
            
            self.edit_laser_ip.setText(laser_ip)
            self.edit_laser_port.setText(laser_port)
            self.combo_laser_auth_type.setCurrentIndex(laser_auth_mode)
            self.edit_laser_id.setText(laser_id)
            self.edit_laser_pw.setText(laser_pw)
            self.edit_laser_token.setText(laser_token)
            self._on_laser_auth_type_changed(laser_auth_mode)
            self.btn_laser_poll.setChecked(laser_poll)
        finally:
            self._is_loading = False

    # ─────────────────────────────────────────────────────────────────
    # 공개 API (main_window 에서 호출)
    # ─────────────────────────────────────────────────────────────────

    def set_shared_cameraera(self, camera):
        self._camera = camera
        self._viewer_first_frame = True
        caps = getattr(camera, "capabilities", None) if camera is not None else None
        self._apply_camera_capabilities(caps)
        self._set_camera_action_state(camera is not None)
        if camera is not None:
            try:
                self.spin_exposure.setValue(float(camera.get_exposure_ms()))
            except Exception:
                pass

    def clear_shared_cameraera(self):
        self._camera = None
        self._viewer_first_frame = True
        self._apply_camera_capabilities(None)
        self._set_camera_action_state(False)

    def _on_sub_panel_log(self, msg: str):
        dev_logger.debug(f"[DeepAlign] {msg}")

    # ── AutoFocus Worker 연동 ─────────────────────────────────────────────────

    def _on_af_run_requested(self, center: float, half_range: float, step: float, metric: str):
        if self._af_worker is not None and self._af_worker.isRunning():
            return

        z_positions = list(np.arange(center - half_range, center + half_range + step * 0.5, step))

        # 카메라 proxy: hub.snap(owner) 또는 self._camera.snap()
        camera_proxy = _HubCameraProxy(self._session_hub, self._camera)

        # KIMM proxy: hub.kimm_move_to_z(z) 또는 self._kimm.move_to_z(z)
        kimm_proxy = _HubKimmProxy(self._session_hub, self._kimm if hasattr(self, "_kimm") else None)

        if not camera_proxy.is_valid():
            self.af_panel.set_error("카메라가 연결되지 않았습니다")
            return

        self._af_worker = AutoFocusWorker(
            camera=camera_proxy,
            kimm_ctrl=kimm_proxy if kimm_proxy.is_valid() else None,
            z_positions=z_positions,
            metric=metric,
            settle_ms=200,
            sim_mode=(not kimm_proxy.is_valid()),
        )
        self._af_worker.step_done.connect(
            lambda step, total, z, sh, _frame: self.af_panel.update_progress(step, total, z, sh)
        )
        self._af_worker.finished.connect(lambda best_z, _sh: self.af_panel.set_result(best_z))
        self._af_worker.error.connect(self.af_panel.set_error)
        self._af_worker.start()

    def _on_af_stop_requested(self):
        """AutoFocusPanel에서 중단 버튼 클릭 시 호출."""
        if self._af_worker:
            self._af_worker.stop()

    # ── SCAN 워커 (mirror / kimm / acs) ────────────────────────────────────
    # 카메라(snap_fn)는 세 워커가 공용으로 사용. 동시에 1개만 실행.

    def _scan_is_running(self) -> bool:
        return self._scan_thread is not None and self._scan_thread.isRunning()

    def _scan_snap_fn(self):
        """3 워커 공용 카메라 스냅."""
        if self._session_hub is None:
            raise RuntimeError("session_hub가 없어 snap 불가")
        return self._session_hub.snap(OWNER_DEEPALIGN)

    def _scan_start(self, scan_widget, worker, on_finished_extra=None) -> None:
        """공용 워커 부트스트랩: thread 부착, 시그널 연결, start.
        scan_widget은 *Scan*Widget 인스턴스 (set_scan_status / set_scan_running 보유).

        on_finished_extra 는 finished / error 양쪽에서 호출됨 — 안전 disable 등
        cleanup 코드를 반드시 실행하기 위함.
        """
        self._scan_owner_panel = scan_widget
        self._scan_worker = worker
        self._scan_thread = QThread(self)
        worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(worker.run)

        # progress 는 phase 시그널이 자체적으로 idx/total 을 갱신하므로 생략 가능.
        # set_scan_status 는 phase 의 detail 라인 (snap 1/3 같은 짧은 텍스트) 으로 사용.
        worker.error.connect(lambda msg: scan_widget.set_scan_status(msg, "err"))

        # log 메시지를 dev_logger + scan_widget 상태 라벨 + 분석 dock 의 log 창에
        # 함께 표시. dev_logger 는 디버깅용, 라벨은 짧은 현 상태, dock log 는 히스토리.
        def _on_worker_log(msg: str):
            dev_logger.info(f"[Scan] {msg}")
            scan_widget.set_scan_status(msg, "info")
            if scan_widget is getattr(self, "mirror_scan", None):
                if hasattr(self, "da_log"):
                    self.da_log.append(msg)
            elif scan_widget is getattr(self, "kimm_scan", None):
                if hasattr(self, "af_log"):
                    self.af_log.append(msg)
        worker.log.connect(_on_worker_log)

        # phase 시그널 → scan_widget 의 PhaseIndicator 갱신 (MOVE/SETTLE/SNAP/COMPUTE)
        def _on_phase(idx: int, total: int, phase: str, _detail: str):
            if hasattr(scan_widget, "set_phase"):
                scan_widget.set_phase(idx, total, phase)
        worker.phase.connect(_on_phase)

        # 스캔 SPE 저장용 buffer 초기화 (이전 스캔의 buffer 폐기).
        # 새 스캔이 시작되는 시점이 buffer 를 비울 유일한 시점 — 종료 후에도
        # manual save 가능하도록 buffer 는 보존된다.
        self._scan_frames_buf = []
        self._scan_records_buf = []
        # 이전 buffer 가 있으면 Save Last 도 비활성화
        if hasattr(scan_widget, "set_save_last_enabled"):
            scan_widget.set_save_last_enabled(False)

        # 스캔 포인트마다 캡쳐된 frame 을 image viewer + processing 파이프라인에
        # 흘려보냄. + 분석 dock (da_* / af_*) 채움 + SPE 저장 buffer 누적.
        def _on_point_done(idx, total, point, frame, result, record):
            if frame is None:
                return
            try:
                self._push_frame(
                    frame,
                    gallery_label=f"Scan_{idx:03d}",
                    source="snap",
                )
            except Exception as e:
                dev_logger.warning(f"[Scan] _push_frame 실패 idx={idx}: {e}")

            try:
                if scan_widget is getattr(self, "mirror_scan", None):
                    self._append_da_dock(idx, point, frame, result)
                elif scan_widget is getattr(self, "kimm_scan", None):
                    self._append_af_dock(idx, point, frame, result)
                elif scan_widget is getattr(self, "acs_scan", None):
                    # ACS 는 별도 dock 없음 — 추후 6축 결과 dock 추가 시 라우팅
                    pass
            except Exception as e:
                dev_logger.warning(f"[Scan] dock 갱신 실패 idx={idx}: {e}")

            # SPE 저장 옵션이 켜져 있을 때만 buffer 에 누적 (메모리 절약)
            if getattr(scan_widget, "is_save_spe_enabled", lambda: False)():
                rec = dict(record) if isinstance(record, dict) else {}
                rec["step"] = idx
                self._scan_frames_buf.append(frame)
                self._scan_records_buf.append(rec)

        worker.point_done.connect(_on_point_done)

        # 중복 실행 방지 플래그 (finished/error 한쪽만 실행되도록)
        self._scan_cleanup_called = False

        def _run_extra():
            if self._scan_cleanup_called:
                return
            self._scan_cleanup_called = True
            if on_finished_extra is not None:
                try: on_finished_extra()
                except Exception as e:
                    dev_logger.warning(f"[Scan] on_finished_extra 예외: {e}")

        def _on_done(_results):
            stopped = getattr(worker, "_stop", False)
            scan_widget.set_scan_status("stopped" if stopped else "done",
                                        "warn" if stopped else "ok")
            mode = getattr(scan_widget, "get_spe_save_mode", lambda: "off")()
            has_buf = bool(self._scan_frames_buf)
            # auto 모드: 종료 즉시 자동 저장
            if has_buf and mode == "auto":
                try:
                    self._save_scan_spe(scan_widget)
                except Exception as e:
                    dev_logger.exception(f"[Scan] auto SPE 저장 실패: {e}")
                    scan_widget.set_scan_status(f"SPE 저장 실패: {e}", "warn")
            # manual 모드 (또는 auto 후 재저장 가능): Save Last 버튼 활성
            if has_buf and hasattr(scan_widget, "set_save_last_enabled"):
                scan_widget.set_save_last_enabled(True)
            # KIMM 스캔 종료 시 — sharpness 시계열의 argmax 를 Best-Z 로 산출해
            # AutoFocusPanel 의 RESULT 섹션에 표시.
            if scan_widget is getattr(self, "kimm_scan", None):
                self._update_best_z_from_kimm_scan()
            _run_extra()
            self._scan_thread.quit()

        def _on_error(_msg):
            _run_extra()
            self._scan_thread.quit()

        worker.finished.connect(_on_done)
        worker.error.connect(_on_error)
        self._scan_thread.finished.connect(self._scan_cleanup)

        scan_widget.set_scan_running(True)
        scan_widget.set_scan_status("starting…", "info")
        self._scan_thread.start()

    def _scan_cleanup(self) -> None:
        if self._scan_owner_panel is not None:
            self._scan_owner_panel.set_scan_running(False)
        self._scan_owner_panel = None
        self._scan_worker = None
        self._scan_thread = None
        self._scan_acs_mover = None
        # buffer 는 의도적으로 보존 — Save Last 로 수동 저장 가능.
        # 다음 스캔이 시작될 때 _scan_start 가 비움.

    def _update_best_z_from_kimm_scan(self) -> None:
        """KIMM 스캔 종료 시 sharpness 시계열의 argmax 를 Best-Z 로 산출해
        AutoFocusPanel.set_result 에 전달."""
        zs = getattr(self, "_af_z_series", [])
        sh = getattr(self, "_af_sh_series", [])
        if not zs or not sh or len(zs) != len(sh):
            return
        try:
            best_idx = int(np.argmax(sh))
            best_z = float(zs[best_idx])
            if hasattr(self, "af_panel") and hasattr(self.af_panel, "set_result"):
                self.af_panel.set_result(best_z)
            if hasattr(self, "af_log"):
                self.af_log.append(
                    f"🏆 Best Z = {best_z:+.3f} µm  (sharpness = {sh[best_idx]:.2f})"
                )
        except Exception as e:
            dev_logger.warning(f"[Scan] Best-Z 산출 실패: {e}")

    def _save_scan_spe_dialog(self, scan_widget) -> None:
        """Save Last 버튼 → file dialog 로 경로 선택해서 저장 (수동 모드)."""
        if not self._scan_frames_buf:
            scan_widget.set_scan_status("저장할 buffer 없음", "warn")
            return
        first_rec = self._scan_records_buf[0] if self._scan_records_buf else {}
        scan_type = str(first_rec.get("scan_type", "scan"))
        default_dir = self.edit_folder.text().strip() or "Live_Captures"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"Scan_{scan_type}_{ts}.spe"
        from PyQt6.QtWidgets import QFileDialog
        fpath, _ = QFileDialog.getSaveFileName(
            self, "Save Scan SPE",
            str(Path(default_dir) / default_name),
            "SPE Files (*.spe);;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not fpath:
            return
        try:
            self._save_scan_spe(scan_widget, override_path=Path(fpath))
        except Exception as e:
            dev_logger.exception(f"[Scan] manual SPE 저장 실패: {e}")
            scan_widget.set_scan_status(f"SPE 저장 실패: {e}", "err")

    def _save_scan_spe(self, scan_widget, override_path: Optional[Path] = None) -> None:
        """스캔 종료 시 단일 multi-frame SPE 로 저장.

        프레임은 self._scan_frames_buf, step별 메타데이터(모터 위치 + 분석값)는
        self._scan_records_buf 에서 가져옴. extra_metadata 에 records 전체를
        담아 후처리 시 step → motor/centroid 매핑 가능.
        """
        if not self._scan_frames_buf:
            return

        # scan_type 식별 — records 첫 행에서 가져옴
        first_rec = self._scan_records_buf[0] if self._scan_records_buf else {}
        scan_type = str(first_rec.get("scan_type", "scan"))

        if override_path is not None:
            fpath = Path(override_path)
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fname = fpath.name
        else:
            folder = Path(self.edit_folder.text().strip() or "Live_Captures")
            folder.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"Scan_{scan_type}_{ts}.spe"
            fpath = folder / fname

        vendor = self.cb_vendor.currentText().strip() or "DeepAlign"
        camera_name = vendor
        camera_model = vendor
        if self._session_hub is not None:
            try:
                state = self._session_hub.get_camera_state()
                camera_name = getattr(state, "vendor", "") or camera_name
                camera_model = getattr(state, "device_id", "") or camera_model
            except Exception:
                pass

        # records 를 SPE extra_metadata 친화 형식으로 변환 (str 만 허용)
        records_str = []
        for r in self._scan_records_buf:
            row = {}
            for k, v in r.items():
                row[str(k)] = "" if v is None else str(v)
            records_str.append(row)

        try:
            save_spe(
                fpath,
                self._scan_frames_buf,
                exposure_ms=float(self.spin_exposure.value()),
                camera_name=camera_name,
                camera_model=camera_model,
                creator="DeepAlign",
                software="SpeAnalyze-DeepAlign",
                extra_metadata={
                    "Scan": {
                        "Type":   scan_type,
                        "Steps":  str(len(self._scan_frames_buf)),
                        "Vendor": vendor,
                    },
                    "ScanRecords": {f"step_{r.get('step','?')}": r for r in records_str},
                    **( {"ProcROI": self._get_proc_roi_metadata()}
                        if self._get_proc_roi_metadata() else {} ),
                },
            )
            scan_widget.set_scan_status(f"💾 SPE 저장: {fname}", "ok")
            dev_logger.info(f"[Scan] SPE 저장 완료: {fpath} ({len(self._scan_frames_buf)} frames)")
        except Exception as e:
            dev_logger.exception(f"[Scan] SPE 저장 실패: {e}")
            raise

    # ── 분석 dock (da_*: Mirror, af_*: KIMM) 갱신 ──────────────────────

    def _clear_da_dock(self) -> None:
        if hasattr(self, "da_frame_list"):
            self.da_frame_list.clear()
            # 클릭 → viewer 동기화 시그널 (한 번만 연결, 재진입 방지)
            if not getattr(self, "_da_list_signal_wired", False):
                self.da_frame_list.currentRowChanged.connect(self._on_da_frame_row)
                self._da_list_signal_wired = True
        if hasattr(self, "da_table"):
            self.da_table.setRowCount(0)
            if not getattr(self, "_da_table_signal_wired", False):
                self.da_table.currentCellChanged.connect(
                    lambda r, c, pr, pc: self._on_da_frame_row(r))
                self._da_table_signal_wired = True
        if hasattr(self, "da_log"):
            self.da_log.clear()
        if hasattr(self, "da_plot_panel"):
            pw = getattr(self.da_plot_panel, "plot_widget", None)
            if pw is not None:
                try: pw.clear()
                except Exception: pass
        self._da_cent_x_series: list[float] = []
        self._da_cent_y_series: list[float] = []
        # 썸네일 ↔ 원본 frame 매핑 buffer (선택 시 viewer 표시용, SPE 옵션과 무관)
        self._da_frames_view: list = []

    def _clear_af_dock(self) -> None:
        if hasattr(self, "af_frame_list"):
            self.af_frame_list.clear()
            if not getattr(self, "_af_list_signal_wired", False):
                self.af_frame_list.currentRowChanged.connect(self._on_af_frame_row)
                self._af_list_signal_wired = True
        if hasattr(self, "af_table"):
            self.af_table.setRowCount(0)
            if not getattr(self, "_af_table_signal_wired", False):
                self.af_table.currentCellChanged.connect(
                    lambda r, c, pr, pc: self._on_af_frame_row(r))
                self._af_table_signal_wired = True
        if hasattr(self, "af_log"):
            self.af_log.clear()
        if hasattr(self, "af_plot_panel"):
            pw = getattr(self.af_plot_panel, "plot_widget", None)
            if pw is not None:
                try: pw.clear()
                except Exception: pass
        self._af_z_series: list[float] = []
        self._af_sh_series: list[float] = []
        self._af_frames_view: list = []

    # ── 썸네일/테이블 → viewer 동기화 ────────────────────────────────────

    def _on_da_frame_row(self, row: int) -> None:
        """Mirror dock — 썸네일/테이블 행 선택 시 viewer + table 동시 동기화."""
        if row < 0 or row >= len(getattr(self, "_da_frames_view", [])):
            return
        # frame_list ↔ table 양쪽 선택 동기화 (재진입 방지: blockSignals)
        try:
            self.da_frame_list.blockSignals(True)
            self.da_frame_list.setCurrentRow(row)
        finally:
            self.da_frame_list.blockSignals(False)
        try:
            self.da_table.blockSignals(True)
            self.da_table.selectRow(row)
        finally:
            self.da_table.blockSignals(False)
        # viewer 로 frame push
        frame = self._da_frames_view[row]
        try:
            self._push_frame(frame, gallery_label=f"Scan_{row+1:03d}", source="snap")
        except Exception as e:
            dev_logger.warning(f"[Scan] da row {row} viewer 동기화 실패: {e}")

    def _on_af_frame_row(self, row: int) -> None:
        """KIMM dock — 동일 패턴."""
        if row < 0 or row >= len(getattr(self, "_af_frames_view", [])):
            return
        try:
            self.af_frame_list.blockSignals(True)
            self.af_frame_list.setCurrentRow(row)
        finally:
            self.af_frame_list.blockSignals(False)
        try:
            self.af_table.blockSignals(True)
            self.af_table.selectRow(row)
        finally:
            self.af_table.blockSignals(False)
        frame = self._af_frames_view[row]
        try:
            self._push_frame(frame, gallery_label=f"Scan_{row+1:03d}", source="snap")
        except Exception as e:
            dev_logger.warning(f"[Scan] af row {row} viewer 동기화 실패: {e}")

    def _append_thumbnail(self, list_widget, frame, label: str) -> None:
        from PyQt6.QtWidgets import QListWidgetItem
        from PyQt6.QtGui import QImage, QIcon, QPixmap
        from PyQt6.QtCore import Qt
        try:
            rgb = make_thumbnail_rgb(frame, w=80, h=60)
            qimg = QImage(rgb.tobytes(), 80, 60, 80 * 3, QImage.Format.Format_RGB888)
            item = QListWidgetItem(QIcon(QPixmap.fromImage(qimg)), label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            list_widget.addItem(item)
            list_widget.scrollToItem(item)
        except Exception as e:
            dev_logger.warning(f"[Scan] thumbnail 생성 실패: {e}")

    def _append_da_dock(self, idx: int, point, frame, result) -> None:
        """Mirror scan 결과 dock 갱신. point=(motor, target_steps), result=centroid stats."""
        from PyQt6.QtWidgets import QTableWidgetItem
        from PyQt6.QtCore import Qt

        # frame 을 view buffer 에 누적 (썸네일 선택 → viewer 동기화용)
        self._da_frames_view.append(frame)

        # 썸네일
        if hasattr(self, "da_frame_list"):
            self._append_thumbnail(self.da_frame_list, frame, f"#{idx}")

        # M1-M4 위치는 hub 에서 일괄 조회 (이동 안 한 축도 표시)
        m_positions = [None, None, None, None]
        if self._session_hub is not None and self._session_hub.is_pico_connected():
            for axis in range(1, 5):
                try:
                    m_positions[axis - 1] = int(self._session_hub.pico_get_position(axis))
                except Exception:
                    pass

        cx = cy = sx = sy = snr = 0.0
        if isinstance(result, dict):
            cx  = float(result.get("cent_x",  0.0))
            cy  = float(result.get("cent_y",  0.0))
            sx  = float(result.get("sigma_x", 0.0))
            sy  = float(result.get("sigma_y", 0.0))
            snr = float(result.get("snr",     0.0))

        # 테이블 행 추가: Step | M1 | M2 | M3 | M4 | CentX | CentY | σX | σY | SNR
        if hasattr(self, "da_table"):
            row = self.da_table.rowCount()
            self.da_table.insertRow(row)
            vals = [
                str(idx),
                *[("—" if p is None else str(p)) for p in m_positions],
                f"{cx:.2f}", f"{cy:.2f}",
                f"{sx:.2f}", f"{sy:.2f}",
                f"{snr:.1f}",
            ]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.da_table.setItem(row, col, it)
            self.da_table.scrollToBottom()

        # Centroid 플롯 (X, Y 시계열) — 이동 모터 step 을 x 축으로 사용해도 되지만
        # 단순화를 위해 idx 를 x 축으로 사용.
        self._da_cent_x_series.append(cx)
        self._da_cent_y_series.append(cy)
        if hasattr(self, "da_plot_panel"):
            pw = getattr(self.da_plot_panel, "plot_widget", None)
            if pw is not None:
                try:
                    pw.clear()
                    xs = list(range(1, len(self._da_cent_x_series) + 1))
                    pw.plot(xs, self._da_cent_x_series, pen="#4ecdc4", name="CentX")
                    pw.plot(xs, self._da_cent_y_series, pen="#f59e0b", name="CentY")
                except Exception:
                    pass

    def _append_af_dock(self, idx: int, point, frame, result) -> None:
        """KIMM Z 스캔 결과 dock 갱신. point=z(µm), result={sharpness, z}."""
        from PyQt6.QtWidgets import QTableWidgetItem
        from PyQt6.QtCore import Qt

        z = float(point) if not isinstance(point, dict) else float(point.get("z", 0.0))
        sh = 0.0
        if isinstance(result, dict):
            sh = float(result.get("sharpness", 0.0))
            z  = float(result.get("z", z))

        # frame 누적 (선택 → viewer 동기화용)
        self._af_frames_view.append(frame)

        if hasattr(self, "af_frame_list"):
            self._append_thumbnail(self.af_frame_list, frame, f"#{idx}")

        if hasattr(self, "af_table"):
            row = self.af_table.rowCount()
            self.af_table.insertRow(row)
            for col, v in enumerate([str(idx), f"{z:+.2f}", f"{sh:.1f}"]):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.af_table.setItem(row, col, it)
            self.af_table.scrollToBottom()

        self._af_z_series.append(z)
        self._af_sh_series.append(sh)
        if hasattr(self, "af_plot_panel"):
            pw = getattr(self.af_plot_panel, "plot_widget", None)
            if pw is not None:
                try:
                    pw.clear()
                    xs = list(range(1, len(self._af_sh_series) + 1))
                    pw.plot(xs, self._af_sh_series,
                            pen="#4ecdc4", symbol="o", symbolSize=6)
                except Exception:
                    pass

    def _on_scan_stop_requested(self) -> None:
        if self._scan_worker is not None and hasattr(self._scan_worker, "stop"):
            self._scan_worker.stop()

    # ── Mirror (Picomotor) ────────────────────────────────────────────────
    def _on_mirror_scan_requested(self, points: list, settle_ms: int, avg_frames: int) -> None:
        if self._scan_is_running():
            self.mirror_scan.set_scan_status("다른 스캔 실행중", "warn"); return
        if self._session_hub is None or not self._session_hub.is_pico_connected():
            self.mirror_scan.set_scan_status("Picomotor 미연결", "err"); return
        if not self._is_hub_camera_connected():
            self.mirror_scan.set_scan_status("카메라 미연결", "err"); return

        mover = MirrorMover(
            self._session_hub,
            move_timeout_ms=self.mirror_scan.get_move_timeout_ms(),
        )
        # process_fn 으로 centroid stats 계산 → _on_point_done 에서 da_* dock 채움
        # session_hub 를 worker 에 주입 — _make_step_record 에서 M1-M4 위치 조회용
        self._clear_da_dock()
        worker = _MirrorScanWorker(
            mover, self._scan_snap_fn, points,
            session_hub=self._session_hub,
            process_fn=mirror_centroid_process_fn,
            settle_ms=settle_ms, avg_frames=avg_frames,
        )
        self._scan_start(self.mirror_scan, worker)

    # ── KIMM Z ────────────────────────────────────────────────────────────
    def _on_kimm_scan_requested(self, z_positions: list, settle_ms: int, avg_frames: int) -> None:
        if self._scan_is_running():
            self.kimm_scan.set_scan_status("다른 스캔 실행중", "warn"); return
        if self._session_hub is None or not self._session_hub.is_kimm_connected():
            self.kimm_scan.set_scan_status("KIMM 미연결", "err"); return
        if not self._is_hub_camera_connected():
            self.kimm_scan.set_scan_status("카메라 미연결", "err"); return

        mover = KimmMover(
            self._session_hub,
            move_timeout_ms=self.kimm_scan.get_move_timeout_ms(),
        )
        # process_fn 으로 sharpness 계산 → _on_point_done 에서 af_* dock 채움
        self._clear_af_dock()
        worker = _KimmScanWorker(
            mover, self._scan_snap_fn, z_positions,
            process_fn=kimm_sharpness_process_fn,
            settle_ms=settle_ms, avg_frames=avg_frames,
        )
        self._scan_start(self.kimm_scan, worker)

    def _on_kimm_servo_on_requested(self) -> None:
        if self._session_hub is None or not self._session_hub.is_kimm_connected():
            self.kimm_scan.set_scan_status("KIMM 미연결", "err"); return
        try:
            self._session_hub.kimm_servo_on()
            self.kimm_scan.set_scan_status("Servo ON 완료", "ok")
            dev_logger.info("[KIMM] Servo turned ON successfully")
        except Exception as e:
            self.kimm_scan.set_scan_status(f"Servo ON 실패: {e}", "err")

    def _on_kimm_servo_off_requested(self) -> None:
        if self._session_hub is None or not self._session_hub.is_kimm_connected():
            self.kimm_scan.set_scan_status("KIMM 미연결", "err"); return
        try:
            self._session_hub.kimm_servo_off()
            self.kimm_scan.set_scan_status("Servo OFF 완료", "ok")
            dev_logger.info("[KIMM] Servo turned OFF successfully")
        except Exception as e:
            self.kimm_scan.set_scan_status(f"Servo OFF 실패: {e}", "err")

    # ── ACS 6축 ──────────────────────────────────────────────────────────
    def _on_acs_scan_requested(self, points: list, settle_ms: int, avg_frames: int) -> None:
        if self._scan_is_running():
            self.acs_scan.set_scan_status("다른 스캔 실행중", "warn"); return
        if self._session_hub is None or not self._session_hub.is_acs_connected():
            self.acs_scan.set_scan_status("ACS 미연결", "err"); return
        if not self._is_hub_camera_connected():
            self.acs_scan.set_scan_status("카메라 미연결", "err"); return

        mover = AcsMover(
            self._session_hub,
            move_timeout_ms=self.acs_scan.get_move_timeout_ms(),
        )
        try:
            mover.enable(timeout_ms=2000)
        except Exception as e:
            self.acs_scan.set_scan_status(f"Servo ON 실패: {e}", "err"); return

        self._scan_acs_mover = mover
        worker = _AcsScanWorker(
            mover, self._scan_snap_fn, points,
            process_fn=None, settle_ms=settle_ms, avg_frames=avg_frames,
        )

        def _disable_after():
            try: mover.disable()
            except Exception: pass

        self._scan_start(self.acs_scan, worker, on_finished_extra=_disable_after)

    # ── Kinematic Calc ────────────────────────────────────────────────────────

    def _on_kinem_config_browse(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "형상 설정 파일 선택", "", "JSON Files (*.json);;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if path:
            self.edit_kinem_config.setText(path)

    def _on_calc_kinem_clicked(self):
        import json
        from core.motor.AlignStageAlgorithm import CalculateAttitude

        config_path = self.edit_kinem_config.text().strip()
        if not config_path:
            self.lbl_kinem_result.setText("⚠ geometry config 파일을 선택하세요")
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            v0 = np.array(cfg["v0"], dtype=float)
            b0 = np.array(cfg["b0"], dtype=float)
            m  = np.array(cfg["m"],  dtype=float)
            m0 = np.array(cfg["m0"], dtype=float)
            a0 = np.array(cfg["a0"], dtype=float)
            c0 = np.array(cfg["c0"], dtype=float)
            x0 = np.array(cfg.get("x0", [0.0] * 6), dtype=float)
        except Exception as e:
            self.lbl_kinem_result.setText(f"⚠ 설정 파일 오류: {e}")
            dev_logger.exception("[DeepAlign] kinem config load failed")
            return

        b = np.array([
            sp.value()
            for row in self._kinem_ball_spins
            for sp in row
        ], dtype=float)

        try:
            result = CalculateAttitude(v0, b0, m, m0, a0, c0, b, x0)
            rx, ry, rz, tx, ty, tz = result
            self.lbl_kinem_result.setText(
                f"rx={rx:.4f}  ry={ry:.4f}  rz={rz:.4f}\n"
                f"tx={tx:.4f}  ty={ty:.4f}  tz={tz:.4f}"
            )
            dev_logger.info(f"[DeepAlign] CalculateAttitude → {result}")
        except Exception as e:
            self.lbl_kinem_result.setText(f"⚠ 계산 오류: {e}")
            dev_logger.exception("[DeepAlign] CalculateAttitude failed")

    def _on_master_btn_not_implemented(self, name: str):
        """Master Bar 버튼 중 아직 패널 공개 API가 없는 항목의 임시 핸들러."""
        dev_logger.warning(f"[DeepAlign] Master Bar '{name}' 버튼은 아직 연결되지 않았습니다.")

    def cleanup(self):
        if hasattr(self, "motion_panel"):
            self.motion_panel.cleanup()

    def _on_tab_changed(self, idx: int):
        super()._on_tab_changed(idx)
        if not hasattr(self, "main_splitter"):
            return
            
        _ANALYSIS_PAGE_IDX = 5
        _MOTION_PAGE_IDX = 4
        
        _AF_IDX        = 2   # AutoFocus
        _SCAN_TABS     = {1, 3}  # Mirror, Align — CAPTURED FRAMES + Centroid 테이블
        _AF_TABS       = {2}     # AutoFocus — Sharpness vs Z 패널

        if idx == _ANALYSIS_PAGE_IDX:
            self._set_analysis_mode_ui(True)
        elif idx == _MOTION_PAGE_IDX:
            self._set_analysis_mode_ui(False)
            self.dock_scan_result.hide()
            self.dock_af_result.hide()
            self.central_stack.setMaximumWidth(16_777_215)
            self.right_splitter.setVisible(False)
            self.main_splitter.setSizes([1600, 0])
        else:
            self._set_analysis_mode_ui(False)
            self.right_splitter.setVisible(True)
            self.central_stack.setMaximumWidth(16_777_215)
            self.main_splitter.setSizes([450, 1240])
            if idx in _SCAN_TABS:
                self.dock_af_result.hide()
                self.dock_host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_scan_result)
                self.dock_scan_result.show()
            elif idx in _AF_TABS:
                self.dock_scan_result.hide()
                self.dock_host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_af_result)
                self.dock_af_result.show()
            else:
                self.dock_scan_result.hide()
                self.dock_af_result.hide()

    def _set_analysis_mode_ui(self, active: bool):
        """Analysis 탭 진입 시 기존 Dashboard 레이아웃을 전용 분석 도구 레이아웃으로 전환한다."""
        if active:
            # 1. 사이드바와 하단 마스터바 숨김 (공간 확보)
            self.central_stack.setVisible(False)
            self.master_bar.setVisible(False)
            
            # 2. 전용 툴바 표시
            self.analysis_toolbar.setVisible(True)
            
            # 3. 독 재배치 (기존 AnalysisTab 레이아웃 복제)
            # Files & Frames -> Left
            self.dock_host.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_files)
            self.dock_host.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_frames)
            self.dock_host.splitDockWidget(self.dock_files, self.dock_frames, Qt.Orientation.Vertical)
            
            # Plot & Hist -> Bottom
            self.dock_host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_plot)
            self.dock_host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_hist)
            self.dock_host.splitDockWidget(self.dock_plot, self.dock_hist, Qt.Orientation.Horizontal)
            
            # ROI -> Right
            self.dock_host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_roi)
            
            # Analysis 탭 진입 시 result 패널들 숨김
            self.dock_scan_result.hide()
            self.dock_af_result.hide()

            # 4. 모든 분석 패널 활성화 및 툴바/마스터바 상태 동기화
            for dock in [self.dock_files, self.dock_frames, self.dock_plot, self.dock_hist, self.dock_roi]:
                dock.show()
                name = dock.objectName()
                if name in self.dock_toggles:
                    self.dock_toggles[name].setChecked(True)
            
            # 마스터바 버튼 상태도 동기화 (만약 보인다면)
            self.btn_toggle_plot_sm.setChecked(True)
            self.btn_toggle_hist_sm.setChecked(True)
            self.btn_toggle_roi_sm.setChecked(True)
            
            # 5. 독 크기 조정 (고효율 비율)
            self.dock_host.resizeDocks([self.dock_files, self.dock_frames], [220, 480], Qt.Orientation.Vertical)
            self.dock_host.resizeDocks([self.dock_plot, self.dock_hist], [600, 400], Qt.Orientation.Horizontal)
        else:
            # 일반 대시보드 모드로 복구
            self.central_stack.setVisible(True)
            self.master_bar.setVisible(True)
            self.analysis_toolbar.setVisible(False)
            
            # 독 위치 초기화 (하단 스택)
            self.dock_host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_plot)
            self.dock_host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_hist)
            self.dock_host.splitDockWidget(self.dock_plot, self.dock_hist, Qt.Orientation.Horizontal)
            
            # 불필요 패널 숨김, scan result 패널 복구
            self.dock_files.hide()
            self.dock_frames.hide()
            self.dock_roi.hide()
            
            # 마스터바 버튼 상태 복구 (기본적으로 Plot/Hist만 보임)
            self.btn_toggle_plot_sm.setChecked(self.dock_plot.isVisible())
            self.btn_toggle_hist_sm.setChecked(self.dock_hist.isVisible())
            self.btn_toggle_roi_sm.setChecked(False)
    def _on_an_roi_range_clicked(self):
        """선택된 ROI 영역의 Min/Max로 이미지 뷰어의 컬러맵 범위를 자동 설정한다."""
        if hasattr(self.cam_viewer.viewer, "_on_roi_range_toggled"):
            # SpeImageViewerV2의 btn_roi_range 클릭 시뮬레이션
            btn = self.cam_viewer.viewer.btn_roi_range
            btn.setChecked(not btn.isChecked())
            self.cam_viewer.viewer._on_roi_range_toggled(btn.isChecked())

    def _on_an_fit_clicked(self):
        """이미지 뷰어를 Fit to View 상태로 초기화한다."""
        self.cam_viewer.viewer.autoRange()

    def _sync_analysis_dock_toggle(self, dock, button, visible: bool) -> None:
        button.blockSignals(True)
        button.setChecked(bool(visible))
        button.blockSignals(False)

        if hasattr(self, "dock_toggles"):
            action = self.dock_toggles.get(dock.objectName())
            if action is not None:
                action.blockSignals(True)
                action.setChecked(bool(visible))
                action.blockSignals(False)

        # viewer toolbar 의 Plot/Hist/Proc 버튼도 동기화
        viewer_btn = self._viewer_toolbar_btn_for(dock)
        if viewer_btn is not None:
            viewer_btn.blockSignals(True)
            viewer_btn.setChecked(bool(visible))
            viewer_btn.blockSignals(False)

    def _viewer_toolbar_btn_for(self, dock):
        """dock → viewer toolbar 의 대응 버튼 (없으면 None)."""
        try:
            v = self.cam_viewer.viewer
        except AttributeError:
            return None
        name = dock.objectName()
        if name == "dock_plot":         return getattr(v, "btn_toggle_profile",   None)
        if name == "dock_histogram":    return getattr(v, "btn_toggle_histogram", None)
        if name == "dock_proc_stats":   return getattr(v, "btn_toggle_proc",      None)
        if name == "dock_proc_table":   return getattr(v, "btn_toggle_table",     None)
        return None

    def _on_gallery_item_double_clicked(self, item: QListWidgetItem):
        """갤러리의 썸네일을 더블 클릭하면 해당 원본 프레임을 뷰어에 표시한다."""
        raw = item.data(Qt.ItemDataRole.UserRole)
        if raw is not None:
            self.cam_viewer.set_source_image(raw)
            # 메트릭 갱신 시도 (추후 고도화)
            self._update_analysis_metrics(raw)

    def _update_analysis_metrics(self, raw: np.ndarray):
        """프레임의 기본 분석 메트릭(Peak, FWHM, SNR)을 계산하여 사이드바에 표시한다."""
        try:
            peak = int(raw.max())
            self.lbl_an_peak.setText(f"{peak}")

            # FWHM: 이미지 중앙 행 1D 프로파일 기준 반치폭
            row = raw[raw.shape[0] // 2, :].astype(float)
            row_max = row.max()
            if row_max > 0:
                half_max = row_max / 2.0
                above = np.where(row >= half_max)[0]
                fwhm = int(above[-1] - above[0]) if len(above) >= 2 else 0
            else:
                fwhm = 0
            self.lbl_an_fwhm.setText(f"{fwhm} px")

            # SNR: 피크 / 좌상단 코너 배경 표준편차
            corner = raw[:min(10, raw.shape[0]), :min(10, raw.shape[1])].astype(float)
            bg_std = float(corner.std()) or 1.0
            snr = round(peak / bg_std, 1)
            self.lbl_an_snr.setText(f"{snr}")
        except Exception:
            pass

    # ── SPE File Loading Handlers (From AnalysisTab) ───────────────────
    def _on_an_open_clicked(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open SPE Files", "", "SPE Files (*.spe);;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        for path in paths:
            self._load_spe_async(path)

    def _load_spe_async(self, filepath: str):
        worker = SpeLoadWorker(filepath, None) # SpeFile class will be handled by worker if None
        worker.finished.connect(self._on_spe_loaded)
        worker.error.connect(self._on_load_error)
        worker.finished.connect(lambda *_, w=worker: self._cleanup_worker(w))
        worker.error.connect(lambda *_, w=worker: self._cleanup_worker(w))
        if not hasattr(self, "_analysis_workers"): self._analysis_workers = []
        self._analysis_workers.append(worker)
        worker.start()

    def _on_spe_loaded(self, filepath: str, spe_obj):
        num_frames = getattr(spe_obj, 'num_frames', 1)
        self.file_list_panel.add_file(filepath, spe_obj, num_frames)
        
        filename = Path(filepath).stem
        self.frame_grid_panel.add_file(spe_obj, filepath, num_frames, filename)
        
        # 파일 열리면 파일 독을 자동으로 보여줌
        self.dock_files.setVisible(True)
        self.dock_frames.setVisible(True)

    def _on_load_error(self, msg: str):
        QMessageBox.critical(self, "Load Error", msg)

    def _cleanup_worker(self, worker):
        if hasattr(self, "_analysis_workers"):
            try: self._analysis_workers.remove(worker)
            except ValueError: pass

    def _on_file_selected(self, spe_item: SpeFileItem, frame_idx: int):
        frame = spe_item.spe_obj.frame(frame_idx)
        if frame is not None:
            self.cam_viewer.set_source_image(frame)
            self._update_analysis_metrics(frame)

    def _on_frame_changed(self, spe_item: SpeFileItem, frame_idx: int):
        frame = spe_item.spe_obj.frame(frame_idx)
        if frame is not None:
            self.cam_viewer.set_source_image(frame)
            self._update_analysis_metrics(frame)

    def _on_an_roi_range_toggled(self, checked: bool):
        """이미지 뷰어의 ROI Range 설정을 툴바 버튼과 동기화한다."""
        if hasattr(self.cam_viewer.viewer, "_on_roi_range_toggled"):
            self.cam_viewer.viewer._on_roi_range_toggled(checked)

    def _on_grid_frame_clicked(self, filepath: str, frame_idx: int):
        """프레임 그리드에서 프레임 클릭 시 뷰어와 파일 리스트를 동기화한다."""
        spe_item = self.file_list_panel.find_item(filepath)
        if spe_item:
            self._on_file_selected(spe_item, frame_idx)
            self.file_list_panel.select_file(filepath)
            self.file_list_panel.set_frame(frame_idx)

    def _on_checked_frames_changed(self, checked_list: list):
        """체크된 여러 프레임의 프로파일을 오버레이하여 표시한다."""
        if not checked_list:
            self.plot_panel.clear()
            return
            
        self.plot_panel.clear()
        for filepath, frame_idx in checked_list:
            spe_item = self.file_list_panel.find_item(filepath)
            if not spe_item: continue
            frame = spe_item.spe_obj.frame(frame_idx)
            if frame is None: continue
            
            fname = Path(filepath).stem
            label = f"{fname}_{frame_idx}" if spe_item.num_frames > 1 else fname
            
            # 현재 뷰어의 ROI 모드에 따라 오버레이 계산 (X/Y profile 우선)
            # SpeImageViewerV2와 연동하여 계산 로직 추가 필요 (현재는 단순 평균)
            self.plot_panel.plot_line_overlay(frame.mean(axis=0), label)

    def _on_roi_selected(self, roi_id: int):
        # ROI ID를 기반으로 실제 ROI 타입(Hist 여부)을 판별하여 적절한 모드 전달
        rois = self.cam_viewer.viewer.view.interactions._rois
        roi = rois.get(roi_id)
        
        mode = "profile"
        if roi and getattr(roi, "roi_type", "") == "Hist":
            mode = "hist"
            
        self.cam_viewer.set_active_roi(roi_id, mode)

    def _on_roi_deleted(self, roi_id: int):
        self.cam_viewer.delete_roi(roi_id)

    def _on_roi_goto(self, roi_id: int):
        try:
            rois = self.cam_viewer.viewer.view.interactions._rois
            roi = rois.get(roi_id)
            if roi is None:
                return
            pts = roi.get_points()
            cx = (pts[0] + pts[2]) / 2
            cy = (pts[1] + pts[3]) / 2
            self.cam_viewer.viewer.view.centerOn(cx, cy)
        except Exception:
            dev_logger.exception("[DeepAlign] roi_goto failed")

    def _on_roi_added_from_viewer(self, roi):
        """이미지 뷰어에서 ROI가 생성되면 ROI 패널에 등록한다."""
        color_map = {'Line': '#e94560', 'Box': '#3b82f6', 'Hist': '#14b8a6'}
        color = color_map.get(roi.roi_type, '#e94560')
        self.roi_panel.add_roi(roi.roi_id, roi.label(), color)

    def _on_roi_item_clicked_from_viewer(self, roi_id):
        """이미지 뷰어에서 ROI가 클릭되면 ROI 패널에서 하이라이트한다."""
        if roi_id is not None:
            self.roi_panel.select_roi(roi_id)

    def _on_toggle_analysis_requested(self, panel_type: str):
        """이미지 뷰어의 툴바 버튼을 통해 패널 가시성을 토글한다."""
        if panel_type == "profile":
            self.dock_plot.setVisible(not self.dock_plot.isVisible())
            self.btn_toggle_plot_sm.setChecked(self.dock_plot.isVisible())
        elif panel_type == "histogram":
            self.dock_hist.setVisible(not self.dock_hist.isVisible())
            self.btn_toggle_hist_sm.setChecked(self.dock_hist.isVisible())
        elif panel_type == "proc":
            self.dock_proc_stats.setVisible(not self.dock_proc_stats.isVisible())
            self.btn_toggle_proc_sm.setChecked(self.dock_proc_stats.isVisible())
        elif panel_type == "table":
            self.dock_proc_table.setVisible(not self.dock_proc_table.isVisible())
            self.btn_toggle_table_sm.setChecked(self.dock_proc_table.isVisible())
        elif panel_type == "roi":
            self.dock_roi.setVisible(not self.dock_roi.isVisible())
            self.btn_toggle_roi_sm.setChecked(self.dock_roi.isVisible())

    def _on_reset_dock_layout(self):
        """도킹 레이아웃을 초기 상태(가로 분할)로 복구한다."""
        self.dock_plot.show()
        self.dock_hist.show()
        self.dock_roi.hide()
        self.dock_files.hide()
        
        self.dock_host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_plot)
        self.dock_host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_hist)
        self.dock_host.splitDockWidget(self.dock_plot, self.dock_hist, Qt.Orientation.Horizontal)
        
        # 버튼 상태 동기화
        self.btn_toggle_plot_sm.setChecked(True)
        self.btn_toggle_hist_sm.setChecked(True)
        self.btn_toggle_roi_sm.setChecked(False)

    def _init_laser_control(self) -> None:
        """Initialize HTTP laser control and polling."""
        self.laser_controller = EuvLaserController(self)
        
        # Connect Signals from Controller to UI slots
        self.laser_controller.status_updated.connect(self._on_laser_status_updated)
        self.laser_controller.error_occurred.connect(self._on_laser_error_occurred)
        self.laser_controller.login_status_changed.connect(self._on_laser_login_status_changed)
        self.laser_controller.alarm_triggered.connect(self._on_laser_alarm_triggered)

        # Connect UI signals
        self.btn_laser_poll.toggled.connect(self._on_laser_poll_toggled)
        self.btn_laser_pulse.clicked.connect(self._on_laser_pulse_clicked)
        self.btn_laser_hf.clicked.connect(self._on_laser_hf_clicked)
        self.btn_laser_pe_set.clicked.connect(self._on_laser_pe_set_clicked)
        self.btn_laser_freq_set.clicked.connect(self._on_laser_freq_set_clicked)
        self.combo_laser_auth_type.currentIndexChanged.connect(self._on_laser_auth_type_changed)
        self.btn_laser_token_browse.clicked.connect(self._on_browse_laser_token)

        # Update initial UI
        self._update_laser_status_ui()

        # Run polling immediately if restored checked
        if self.btn_laser_poll.isChecked():
            self._on_laser_poll_toggled(True)

    def _on_laser_auth_type_changed(self, index: int) -> None:
        """Toggle UI widgets based on authentication method."""
        is_idpw = (index == 0)
        self.laser_idpw_widget.setVisible(is_idpw)
        self.laser_token_widget.setVisible(not is_idpw)
        self.laser_controller.set_auth_config(index)

    def _on_browse_laser_token(self) -> None:
        """Open file dialog to browse laser token."""
        import os
        current_path = self.edit_laser_token.text().strip()
        if not current_path or not os.path.exists(current_path):
            current_path = os.getcwd()
        else:
            if os.path.isfile(current_path):
                current_path = os.path.dirname(current_path)

        selected_file, _ = QFileDialog.getOpenFileName(
            self, "Select Laser Token JSON", current_path, "JSON Files (*.json);;All Files (*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if selected_file:
            self.edit_laser_token.setText(selected_file)

    def _login_to_laser_server(self, success_callback) -> None:
        """Login to laser server."""
        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        self.laser_controller.set_connection_info(ip, port)
        self.laser_controller.set_auth_config(self.combo_laser_auth_type.currentIndex())

        username = self.edit_laser_id.text().strip()
        password = self.edit_laser_pw.text()
        token_input = self.edit_laser_token.text().strip()

        self.laser_controller.login_to_laser_server(success_callback, username, password, token_input)

    def _on_laser_poll_toggled(self, checked: bool) -> None:
        """Control polling timer."""
        if checked:
            ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
            port = self.edit_laser_port.text().strip() or "5643"
            self.laser_controller.set_connection_info(ip, port)
            self.laser_controller.set_auth_config(self.combo_laser_auth_type.currentIndex())
            self.laser_controller.set_alarm_config(
                self.chk_laser_temp_alarm.isChecked(),
                float(self.spin_laser_temp_alarm_min.value())
            )

            self.btn_laser_poll.setText("STOP POLL")

            if not self.laser_controller.session_token:
                self._login_to_laser_server(lambda: self.laser_controller.start_polling(3000))
            else:
                self.laser_controller.start_polling(3000)
        else:
            self.btn_laser_poll.setText("START POLL")
            self.laser_controller.stop_polling()

    def _on_laser_pulse_clicked(self, checked: bool) -> None:
        """Pulse control."""
        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        self.laser_controller.set_connection_info(ip, port)
        self.laser_controller.set_auth_config(self.combo_laser_auth_type.currentIndex())

        if not self.laser_controller.session_token:
            self._login_to_laser_server(lambda: self.laser_controller.control_pulse(checked))
        else:
            self.laser_controller.control_pulse(checked)
        self.lbl_laser_info.setText(f"Turning Pulse {'ON' if checked else 'OFF'}...")

    def _on_laser_hf_clicked(self, checked: bool) -> None:
        """High Frequency control."""
        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        self.laser_controller.set_connection_info(ip, port)
        self.laser_controller.set_auth_config(self.combo_laser_auth_type.currentIndex())

        if not self.laser_controller.session_token:
            self._login_to_laser_server(lambda: self.laser_controller.control_hf(checked))
        else:
            self.laser_controller.control_hf(checked)
        self.lbl_laser_info.setText(f"Turning High Freq {'ON' if checked else 'OFF'}...")

    def _on_laser_pe_set_clicked(self) -> None:
        """Pulse Energy setpoint PUT."""
        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        self.laser_controller.set_connection_info(ip, port)
        self.laser_controller.set_auth_config(self.combo_laser_auth_type.currentIndex())
        value = self.spin_laser_pe.value()

        if not self.laser_controller.session_token:
            self._login_to_laser_server(lambda: self.laser_controller.set_pulse_energy(value))
        else:
            self.laser_controller.set_pulse_energy(value)
        self.lbl_laser_info.setText(f"Setting Pulse Energy → {value:.1f} %...")

    def _on_laser_freq_set_clicked(self) -> None:
        """Frequency setpoint PUT."""
        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        self.laser_controller.set_connection_info(ip, port)
        self.laser_controller.set_auth_config(self.combo_laser_auth_type.currentIndex())
        value = self.spin_laser_freq.value()

        if not self.laser_controller.session_token:
            self._login_to_laser_server(lambda: self.laser_controller.set_frequency(value))
        else:
            self.laser_controller.set_frequency(value)
        self.lbl_laser_info.setText(f"Setting Frequency → {value:.2f} Hz...")

    def _on_laser_status_updated(self, states: dict) -> None:
        self._update_laser_status_ui()

    def _on_laser_error_occurred(self, req_type: str, err_msg: str) -> None:
        if req_type == "PUT_PULSE":
            self.lbl_laser_info.setText(f"Pulse Control Error: {err_msg}")
            is_pulse = (self.laser_controller.states.get("pulse", "OFF") == "ON")
            self.btn_laser_pulse.blockSignals(True)
            self.btn_laser_pulse.setChecked(is_pulse)
            self.btn_laser_pulse.setText("🟢 PULSE ON" if is_pulse else "🔴 PULSE OFF")
            self.btn_laser_pulse.setStyleSheet(self._laser_btn_style(is_pulse))
            self.btn_laser_pulse.blockSignals(False)
        elif req_type == "PUT_HF":
            self.lbl_laser_info.setText(f"HF Control Error: {err_msg}")
            is_hf = (self.laser_controller.states.get("hf", "OFF") == "ON")
            self.btn_laser_hf.blockSignals(True)
            self.btn_laser_hf.setChecked(is_hf)
            self.btn_laser_hf.setText("🟢 HIGH ON" if is_hf else "🔴 HIGH OFF")
            self.btn_laser_hf.setStyleSheet(self._laser_btn_style(is_hf))
            self.btn_laser_hf.blockSignals(False)
        elif req_type == "PUT_PULSE_ENERGY":
            self.lbl_laser_info.setText(f"Pulse Energy Set Error: {err_msg}")
        elif req_type == "PUT_FREQ":
            self.lbl_laser_info.setText(f"Frequency Set Error: {err_msg}")

    def _on_laser_login_status_changed(self, success: bool, message: str) -> None:
        self.lbl_laser_info.setText(message)

    def _on_laser_alarm_triggered(self, val: float, min_val: float) -> None:
        """Alarm when disk temperature drops below threshold."""
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        QMessageBox.warning(
            self,
            "Disk Temperature Alarm",
            f"Laser target disk temperature ({val:.1f} \u00b0C) is below minimum threshold ({min_val:.1f} \u00b0C)!"
        )

    def _update_laser_status_ui(self) -> None:
        """Render collected laser/chamber status."""
        states = self.laser_controller.states
        temp = states.get("temp", "N/A")
        pulse_state = states.get("pulse", "N/A")
        hf_state = states.get("hf", "N/A")
        pulse_energy = states.get("pulse_energy", "N/A")
        freq = states.get("freq", "N/A")

        # Sync buttons
        is_pulse = (pulse_state == "ON")
        self.btn_laser_pulse.blockSignals(True)
        self.btn_laser_pulse.setChecked(is_pulse)
        self.btn_laser_pulse.setText("🟢 PULSE ON" if is_pulse else "🔴 PULSE OFF")
        self.btn_laser_pulse.setStyleSheet(self._laser_btn_style(is_pulse))
        self.btn_laser_pulse.blockSignals(False)

        is_hf = (hf_state == "ON")
        self.btn_laser_hf.blockSignals(True)
        self.btn_laser_hf.setChecked(is_hf)
        self.btn_laser_hf.setText("🟢 HIGH ON" if is_hf else "🔴 HIGH OFF")
        self.btn_laser_hf.setStyleSheet(self._laser_btn_style(is_hf))
        self.btn_laser_hf.blockSignals(False)

        # Sync setpoint current-value labels
        self.lbl_laser_pe_current.setText(pulse_energy)
        self.lbl_laser_freq_current.setText(freq)

        pulse_icon = "🟢" if pulse_state == "ON" else "🔴" if pulse_state == "OFF" else "⚪"
        hf_icon = "🟢" if hf_state == "ON" else "🔴" if hf_state == "OFF" else "⚪"

        is_alarm = False
        if self.laser_controller.alarm_enabled and temp not in ("N/A", "Error"):
            try:
                temp_val = float(temp.replace(" \u00b0C", ""))
                if temp_val <= self.laser_controller.alarm_min_temp:
                    is_alarm = True
            except Exception:
                pass

        if is_alarm:
            self.lbl_laser_info.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; font-family: monospace;")
            status_text = (
                f"\u26a1 [DISK TEMP ALARM ACTIVE]\n"
                f"Disk Temp   : {temp} (LOW)\n"
                f"EUV Power   : {states.get('power', 'N/A')}\n"
                f"Duty Cycle  : {states.get('duty', 'N/A')}\n"
                f"Pulse Energy: {pulse_energy}\n"
                f"Frequency   : {freq}\n"
                f"{pulse_icon} Pulse State : {pulse_state}\n"
                f"{hf_icon} High Freq   : {hf_state}"
            )
        else:
            self.lbl_laser_info.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; font-family: monospace;")
            status_text = (
                f"Disk Temp   : {temp}\n"
                f"EUV Power   : {states.get('power', 'N/A')}\n"
                f"Duty Cycle  : {states.get('duty', 'N/A')}\n"
                f"Pulse Energy: {pulse_energy}\n"
                f"Frequency   : {freq}\n"
                f"{pulse_icon} Pulse State : {pulse_state}\n"
                f"{hf_icon} High Freq   : {hf_state}"
            )
        self.lbl_laser_info.setText(status_text)

    def _laser_btn_style(self, is_on: bool) -> str:
        """Get laser button stylesheet."""
        if is_on:
            return """
                QPushButton {
                    background: #10b981; color: white; border: 1px solid #10b981;
                    border-radius: 4px; font-weight: bold; font-size: 11px; padding: 4px;
                }
                QPushButton:hover {
                    background: #059669;
                }
            """
        else:
            return """
                QPushButton {
                    background: #1e1e2f; color: #ef4444; border: 1px solid #ef4444;
                    border-radius: 4px; font-weight: bold; font-size: 11px; padding: 4px;
                }
                QPushButton:hover {
                    background: #ef444422;
                }
            """

