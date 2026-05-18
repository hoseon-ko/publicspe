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
        self._restore_settings()
        self._wire_camera_actions()
        self._init_laser_control()
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

        self.central_stack.addWidget(self._create_cam_page())              # 0
        self.central_stack.addWidget(self._wrap_panel(self.mirror_panel, extras=[self.mirror_scan]))  # 1
        self.central_stack.addWidget(self._wrap_panel(self.af_panel,     extras=[self.kimm_scan]))    # 2
        self.central_stack.addWidget(self._create_align_page())            # 3
        self.central_stack.addWidget(self._wrap_panel(self.motion_panel))  # 4
        self.central_stack.addWidget(self._create_analysis_page())         # 5

        # 3. 우측 작업영역: 도킹(QDockWidget) + 마스터바
        self.dock_host = self._create_docking_workspace()

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
        self.btn_af_run.clicked.connect(self.af_panel.run_af)
        self.btn_af_abort.clicked.connect(self.af_panel.abort_af)
        self.btn_af_set_z.clicked.connect(self.af_panel.set_z_base)
        self.af_panel.run_requested.connect(self._on_af_run_requested)
        self.af_panel.stop_requested.connect(self._on_af_stop_requested)

        # ── SCAN — 3 hardware (mirror/af/acs), 위젯은 패널과 분리됨 ──
        self.mirror_scan.scan_requested.connect(self._on_mirror_scan_requested)
        self.mirror_scan.scan_stop_requested.connect(self._on_scan_stop_requested)
        self.kimm_scan.scan_requested.connect(self._on_kimm_scan_requested)
        self.kimm_scan.scan_stop_requested.connect(self._on_scan_stop_requested)
        self.acs_scan.scan_requested.connect(self._on_acs_scan_requested)
        self.acs_scan.scan_stop_requested.connect(self._on_scan_stop_requested)
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

    def _on_proc_mode_changed(self, mode_id: int) -> None:
        self._proc_mode = mode_id

    def _on_proc_region_changed(self, region_id: int) -> None:
        self._proc_region = "roi" if region_id == 1 else "full"

    def _on_proc_load_clicked(self) -> None:
        start = self.edit_folder.text().strip() or "."
        path, _ = QFileDialog.getOpenFileName(
            self, "처리 이미지 파일 선택", start,
            "All Supported (*.spe *.npy *.npz *.tif *.tiff *.png *.bmp);;"
            "SPE Files (*.spe);;"
            "NumPy (*.npy *.npz);;"
            "Images (*.tif *.tiff *.png *.bmp)"
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
        snap_fn = lambda: self._session_hub.snap(OWNER_DEEPALIGN)

        self.btn_bg_capture.setEnabled(False)
        self.btn_bg_load.setEnabled(False)
        self.lbl_bg_status.setText(f"Capturing 0 / {n} frames...")
        self.lbl_bg_status.setStyleSheet("color: #facc15; font-size: 11px; font-weight: bold;")

        self._bg_capture_thread = QThread(self)
        self._bg_capture_worker = _BgCaptureWorker(snap_fn, n)
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
        path, _ = QFileDialog.getOpenFileName(self, "배경 SPE 파일 선택", start_dir, "SPE Files (*.spe)")
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
            self._bg_save_folder or self.edit_folder.text().strip() or "."
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
        temp     = float(c.get_camera_setting("temp_c",     -70.0,  vendor=new_vendor))
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
        c.set_camera_setting("fps_lock",    bool(self.check_fps_lock.isChecked()), vendor=vendor)
        c.set_camera_setting("temp_c",      float(self.spin_temp.value()),     vendor=vendor)
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
        c.set("tabs.deepalign.proc.enabled", bool(self.check_use_proc.isChecked()))
        c.set("tabs.deepalign.proc.mode",    int(self._proc_mode))
        c.set("tabs.deepalign.proc.region",  str(self._proc_region))
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
        c.set("tabs.deepalign.proc_stats.show_mean", bool(ps.chk_mean.isChecked()))
        c.set("tabs.deepalign.proc_stats.show_min",  bool(ps.chk_min.isChecked()))
        c.set("tabs.deepalign.proc_stats.show_max",  bool(ps.chk_max.isChecked()))

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
            exposure  = float(c.get_camera_setting("exposure_ms", 20.0,  vendor=vendor))
            fps       = float(c.get_camera_setting("fps",         30.0,  vendor=vendor))
            fps_lock  = bool(c.get_camera_setting("fps_lock",     False, vendor=vendor))
            temp      = float(c.get_camera_setting("temp_c",     -70.0,  vendor=vendor))
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
            ):
                self._sync_analysis_dock_toggle(dock, btn, dock.isVisible())

            # ProcStats 복원
            ps = self.proc_stats_panel
            ps.chk_enable.setChecked(bool(c.get("tabs.deepalign.proc_stats.enabled", False)))
            src = str(c.get("tabs.deepalign.proc_stats.source", "snap"))
            if   src == "live": ps.radio_live.setChecked(True)
            elif src == "all":  ps.radio_all.setChecked(True)
            else:               ps.radio_snap.setChecked(True)
            ps.chk_mean.setChecked(bool(c.get("tabs.deepalign.proc_stats.show_mean", True)))
            ps.chk_min.setChecked(bool(c.get("tabs.deepalign.proc_stats.show_min",  True)))
            ps.chk_max.setChecked(bool(c.get("tabs.deepalign.proc_stats.show_max",  True)))

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
        scan_widget은 *Scan*Widget 인스턴스 (set_scan_status / set_scan_running 보유)."""
        self._scan_owner_panel = scan_widget
        self._scan_worker = worker
        self._scan_thread = QThread(self)
        worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(worker.run)

        worker.progress.connect(
            lambda idx, total: scan_widget.set_scan_status(f"{idx}/{total}", "info")
        )
        worker.error.connect(lambda msg: scan_widget.set_scan_status(msg, "err"))
        worker.log.connect(lambda msg: dev_logger.info(f"[Scan] {msg}"))

        def _on_done(_results):
            stopped = getattr(worker, "_stop", False)
            scan_widget.set_scan_status("stopped" if stopped else "done",
                                        "warn" if stopped else "ok")
            if on_finished_extra is not None:
                try: on_finished_extra()
                except Exception: pass
            self._scan_thread.quit()

        worker.finished.connect(_on_done)
        worker.error.connect(lambda _m: self._scan_thread.quit())
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
        worker = _MirrorScanWorker(
            mover, self._scan_snap_fn, points,
            process_fn=None, settle_ms=settle_ms, avg_frames=avg_frames,
        )
        self._scan_start(self.mirror_scan, worker)

    # ── KIMM Z ────────────────────────────────────────────────────────────
    def _on_kimm_scan_requested(self, z_positions: list, settle_ms: int, avg_frames: int) -> None:
        if self._scan_is_running():
            self.kimm_scan.set_scan_status("다른 스캔 실행중", "warn"); return
        if self._session_hub is None or not self._is_hub_camera_connected():
            self.kimm_scan.set_scan_status("카메라 미연결", "err"); return

        mover = KimmMover(
            self._session_hub,
            move_timeout_ms=self.kimm_scan.get_move_timeout_ms(),
        )
        worker = _KimmScanWorker(
            mover, self._scan_snap_fn, z_positions,
            process_fn=None, settle_ms=settle_ms, avg_frames=avg_frames,
        )
        self._scan_start(self.kimm_scan, worker)

    # ── ACS 6축 ──────────────────────────────────────────────────────────
    def _on_acs_scan_requested(self, points: list, settle_ms: int, avg_frames: int) -> None:
        if self._scan_is_running():
            self.acs_scan.set_scan_status("다른 스캔 실행중", "warn"); return
        if self._session_hub is None or not self._session_hub.is_acs_connected():
            self.acs_scan.set_scan_status("ACS 미연결", "err"); return
        acs_ctrl = self._session_hub.acs_controller
        if acs_ctrl is None:
            self.acs_scan.set_scan_status("ACS controller 조회 실패", "err"); return
        if not self._is_hub_camera_connected():
            self.acs_scan.set_scan_status("카메라 미연결", "err"); return

        mover = AcsMover(acs_ctrl, move_timeout_ms=self.acs_scan.get_move_timeout_ms())
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
            self, "형상 설정 파일 선택", "", "JSON Files (*.json);;All Files (*)"
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
        paths, _ = QFileDialog.getOpenFileNames(self, "Open SPE Files", "", "SPE Files (*.spe);;All Files (*)")
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
        """HTTP 레이저 제어 및 실시간 폴링 초기화. __init__ 에서 호출됨."""
        self.laser_manager = QNetworkAccessManager(self)
        self.laser_manager.finished.connect(self._on_laser_http_response)

        self.laser_poll_timer = QTimer(self)
        self.laser_poll_timer.setInterval(3000)
        self.laser_poll_timer.timeout.connect(self._poll_laser_status)

        # 실시간 수신 상태 취합 딕셔너리
        self.laser_states = {"temp": "N/A", "on": "N/A", "hf": "N/A"}

        # 동적 세션 토큰 관리 상태
        self._current_session_token = ""
        self._login_success_callback = None
        self._is_logging_in = False

        # UI 버튼 및 컨트롤 시그널 바인딩
        self.btn_laser_poll.toggled.connect(self._on_laser_poll_toggled)
        self.btn_laser_toggle.clicked.connect(self._on_laser_toggle_clicked)
        self.combo_laser_auth_type.currentIndexChanged.connect(self._on_laser_auth_type_changed)
        self.btn_laser_token_browse.clicked.connect(self._on_browse_laser_token)

        # 초기 UI 출력
        self._update_laser_status_ui()

        # 복원된 설정에 따라 폴링 즉시 실행
        if self.btn_laser_poll.isChecked():
            self._on_laser_poll_toggled(True)

    def _on_laser_auth_type_changed(self, index: int) -> None:
        """인증 방식 변경에 따른 UI 토글."""
        is_idpw = (index == 0)
        self.laser_idpw_widget.setVisible(is_idpw)
        self.laser_token_widget.setVisible(not is_idpw)
        # 인증 방식 변경 시 기존 세션 캐시 초기화
        self._current_session_token = ""

    def _on_browse_laser_token(self) -> None:
        """토큰 JSON 파일 선택 대화상자 표시."""
        import os
        current_path = self.edit_laser_token.text().strip()
        if not current_path or not os.path.exists(current_path):
            current_path = os.getcwd()
        else:
            if os.path.isfile(current_path):
                current_path = os.path.dirname(current_path)

        selected_file, _ = QFileDialog.getOpenFileName(
            self, "Select Laser Token JSON", current_path, "JSON Files (*.json);;All Files (*)"
        )
        if selected_file:
            self.edit_laser_token.setText(selected_file)

    def _get_laser_token_from_file(self, filepath: str) -> str:
        """JSON 파일에서 토큰 값 파싱 및 추출, 또는 Plain Text 토큰 반환."""
        import os
        import json
        if not filepath or not os.path.exists(filepath) or not os.path.isfile(filepath):
            # 파일 경로가 아니거나 없는 경우 입력된 텍스트 자체를 토큰으로 사용
            return filepath

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if isinstance(data, dict):
                # 대소문자 구분 없이 흔한 토큰 키 후보 검색
                for candidate in ["token", "access_token", "apiKey", "api_key", "key", "access-token", "id_token"]:
                    for k, v in data.items():
                        if k.lower() == candidate.lower():
                            return str(v).strip()
                # 후보 키가 없으면 첫 번째 문자열/숫자 값을 반환하는 fallback
                for k, v in data.items():
                    if isinstance(v, (str, int, float)):
                        return str(v).strip()
            elif isinstance(data, str):
                return data.strip()
        except Exception as e:
            dev_logger.warning(f"[DeepAlign] Failed to parse laser token JSON file {filepath}: {e}")
        return filepath

    def _login_to_laser_server(self, success_callback, endpoint_path: str = "/api/login") -> None:
        """서버 로그인 요청을 수행하여 HTTP Basic Auth 토큰 생성 또는 Bearer Token 로드."""
        # 1. 토큰 방식 (Bearer Token)인 경우 -> 아디비번이 필요 없으므로 바로 세션 완료 처리
        if self.combo_laser_auth_type.currentIndex() == 1:
            token_input = self.edit_laser_token.text().strip()
            self._current_session_token = self._get_laser_token_from_file(token_input)
            self.lbl_laser_info.setText("Logged in (Bearer Token)")
            success_callback()
            return

        # 2. 아이디/비번 방식 (HTTP Basic Auth)인 경우 -> 접속자 권한 확인 수행
        user_id = self.edit_laser_id.text().strip()
        user_pw = self.edit_laser_pw.text()  # 패스워드 공백 유지

        import base64
        credentials = f"{user_id}:{user_pw}"
        credentials_bytes = credentials.encode("utf-8")
        base64_credentials = base64.b64encode(credentials_bytes).decode("utf-8")
        self._current_session_token = f"Basic {base64_credentials}"
        
        self._query_user_access_level()
        success_callback()

    def _query_user_access_level(self) -> None:
        """/api/server/users/{user}/accessLevel 요청을 비동기 전송하여 접속자 권한 확인 (ID/PW 모드 전용)."""
        if not self._current_session_token:
            return

        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        token = self._current_session_token

        # ID/PW 방식이므로 입력된 ID를 user로 사용
        user = self.edit_laser_id.text().strip() or "viewer"

        url_str = f"http://{ip}:{port}/api/server/users/{user}/accessLevel"
        request = QNetworkRequest(QUrl(url_str))
        
        if token.startswith("Basic "):
            request.setRawHeader(b"Authorization", token.encode("utf-8"))
        else:
            request.setRawHeader(b"Authorization", f"Bearer {token}".encode("utf-8"))
            request.setRawHeader(b"X-Auth-Token", token.encode("utf-8"))

        reply = self.laser_manager.get(request)
        reply.setProperty("req_type", "GET_ACCESS_LEVEL")
        reply.setProperty("user", user)
        
        dev_logger.info(f"[DeepAlign] Querying access level for user '{user}' -> {url_str}")

    def _poll_laser_status(self) -> None:
        """HTTP GET 요청으로 3개 엔드포인트의 레이저/챔버 상태 비동기 폴링."""
        # 세션 토큰이 없으면 먼저 로그인부터 비동기 수행 후, 성공 시 자신을 다시 호출하도록 유도
        if not self._current_session_token:
            self._login_to_laser_server(self._poll_laser_status)
            return

        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        token = self._current_session_token

        base_url = f"http://{ip}:{port}"
        endpoints = {
            "GET_TEMP": f"{base_url}/api/euvChamber/target/disk/temperature/value",
            "GET_ON": f"{base_url}/api/laser/on",
            "GET_HF": f"{base_url}/api/laser/enableHighFrequency"
        }

        for req_type, url_str in endpoints.items():
            request = QNetworkRequest(QUrl(url_str))
            if token.startswith("Basic "):
                request.setRawHeader(b"Authorization", token.encode("utf-8"))
            else:
                request.setRawHeader(b"Authorization", f"Bearer {token}".encode("utf-8"))
                request.setRawHeader(b"X-Auth-Token", token.encode("utf-8"))

            reply = self.laser_manager.get(request)
            reply.setProperty("req_type", req_type)

    def _on_laser_toggle_clicked(self, checked: bool) -> None:
        """HTTP PUT 요청으로 레이저 ON/OFF 제어."""
        # 세션 토큰이 없으면 먼저 로그인부터 비동기 수행 후, 성공 시 자신을 다시 호출하도록 유도
        if not self._current_session_token:
            self._login_to_laser_server(lambda: self._on_laser_toggle_clicked(checked))
            return

        ip = self.edit_laser_ip.text().strip() or "127.0.0.1"
        port = self.edit_laser_port.text().strip() or "5643"
        token = self._current_session_token

        url_str = f"http://{ip}:{port}/api/laser/on"
        request = QNetworkRequest(QUrl(url_str))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")

        if token.startswith("Basic "):
            request.setRawHeader(b"Authorization", token.encode("utf-8"))
        else:
            request.setRawHeader(b"Authorization", f"Bearer {token}".encode("utf-8"))
            request.setRawHeader(b"X-Auth-Token", token.encode("utf-8"))

        status_val = "on" if checked else "off"
        body = f'{{"status": "{status_val}", "on": {str(checked).lower()}}}'.encode("utf-8")

        reply = self.laser_manager.put(request, body)
        reply.setProperty("req_type", "PUT_ON")
        reply.setProperty("target_status", status_val)

        # 통신 상태 피드백 UI 표시
        self.lbl_laser_info.setText(f"Turning Laser {status_val.upper()}...")

    def _on_laser_poll_toggled(self, checked: bool) -> None:
        """폴링 타이머 ON/OFF 제어."""
        if checked:
            self.laser_poll_timer.start(3000)
            self.btn_laser_poll.setText("STOP POLL")
            self._poll_laser_status()  # 활성화 즉시 1회 강제 호출
        else:
            self.laser_poll_timer.stop()
            self.btn_laser_poll.setText("START POLL")
            self.laser_states = {"temp": "N/A", "on": "N/A", "hf": "N/A"}
            self._current_session_token = ""  # 토큰 캐시 초기화로 다음 폴링 시 클린 로그인 유도
            self._update_laser_status_ui()

    def _on_laser_http_response(self, reply) -> None:
        """HTTP 응답 처리 콜백."""
        reply.deleteLater()  # QNetworkReply 리소스 지연 해제

        req_type = reply.property("req_type")
        if not req_type:
            return

        # 네트워크 통신 에러 처리
        if reply.error() != reply.NetworkError.NoError:
            status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            err_msg = reply.errorString()
            dev_logger.warning(f"[DeepAlign] HTTP Error: Req={req_type}, Status={status_code}, Msg={err_msg}")
            
            # 401/403 에러 발생 시 현재 토큰을 무효화하여 다음 요청 시 재로그인을 시도하도록 가이드
            if status_code in (401, 403):
                dev_logger.warning(f"[DeepAlign] Unauthorized ({status_code}). Clearing cached session token.")
                self._current_session_token = ""
                # 무단/만료 토큰 또는 ID/PW 에러 시 즉시 폴링 정지 및 UI 알림
                if self.combo_laser_auth_type.currentIndex() == 1:
                    self.lbl_laser_info.setText(f"Unauthorized ({status_code}): Check Bearer Token")
                else:
                    self.lbl_laser_info.setText(f"Unauthorized ({status_code}): Check ID / PW")
                self.btn_laser_poll.setChecked(False)
                return

            if req_type == "POST_LOGIN":
                endpoint = reply.property("endpoint") or "/api/login"
                
                # 404 에러 시 차선책 로그인 엔드포인트 자동 재시도
                if status_code == 404 and endpoint == "/api/login":
                    dev_logger.info("[DeepAlign] /api/login failed with 404, trying fallback /api/auth")
                    self._login_to_laser_server(self._login_success_callback, endpoint_path="/api/auth")
                    return
                elif status_code == 404 and endpoint == "/api/auth":
                    dev_logger.info("[DeepAlign] /api/auth failed with 404, trying fallback /api/authenticate")
                    self._login_to_laser_server(self._login_success_callback, endpoint_path="/api/authenticate")
                    return

                self.lbl_laser_info.setText(f"Login Error ({status_code}): {err_msg}")
                self.btn_laser_poll.setChecked(False)
                
            elif req_type == "GET_TEMP":
                self.laser_states["temp"] = "Error"
            elif req_type == "GET_ON":
                self.laser_states["on"] = "Error"
            elif req_type == "GET_HF":
                self.laser_states["hf"] = "Error"
            elif req_type == "PUT_ON":
                self.lbl_laser_info.setText(f"Control Error: {err_msg}")
                target_status = reply.property("target_status")
                # 요청 실패 시 원래 상태로 버튼 UI 복원
                is_on = (target_status == "off")
                self.btn_laser_toggle.blockSignals(True)
                self.btn_laser_toggle.setChecked(is_on)
                self.btn_laser_toggle.setText("🟢 LASER ON" if is_on else "🔴 LASER OFF")
                self.btn_laser_toggle.setStyleSheet(self._laser_btn_style(is_on))
                self.btn_laser_toggle.blockSignals(False)
            elif req_type == "GET_ACCESS_LEVEL":
                user = reply.property("user") or "unknown"
                dev_logger.warning(f"[DeepAlign] Failed to query access level for user '{user}': {err_msg}")
                self.lbl_laser_info.setText(f"Logged in as {user} (Access Level Failed)")
            
            self._update_laser_status_ui()
            return

        # 정상 응답 바디 파싱
        data_bytes = reply.readAll().data()
        data_str = data_bytes.decode("utf-8").strip()
        dev_logger.info(f"[DeepAlign] HTTP Success: Req={req_type}, Response={data_str[:200]}")

        if req_type == "POST_LOGIN":
            token = ""
            try:
                import json
                parsed = json.loads(data_str)
                if isinstance(parsed, dict):
                    for candidate in ["token", "access_token", "apiKey", "api_key", "key", "id_token"]:
                        for k, v in parsed.items():
                            if k.lower() == candidate.lower():
                                token = str(v).strip()
                                break
                        if token:
                            break
                    if not token:
                        for k, v in parsed.items():
                            if isinstance(v, (str, int, float)):
                                token = str(v).strip()
                                break
                else:
                    token = data_str
            except Exception:
                token = data_str

            if token:
                self._current_session_token = token
                self.lbl_laser_info.setText("Login Successful")
                if self._login_success_callback:
                    self._login_success_callback()
            else:
                self.lbl_laser_info.setText("Login Error: Token not found")
                self.btn_laser_poll.setChecked(False)

        elif req_type == "GET_ACCESS_LEVEL":
            user = reply.property("user") or "unknown"
            access_level = "Unknown"
            try:
                import json
                parsed = json.loads(data_str)
                if isinstance(parsed, dict):
                    for candidate in ["accessLevel", "level", "role", "type"]:
                        for k, v in parsed.items():
                            if k.lower() == candidate.lower():
                                access_level = str(v).strip()
                                break
                        if access_level != "Unknown":
                            break
                    if access_level == "Unknown":
                        for k, v in parsed.items():
                            if isinstance(v, (str, int, float)):
                                access_level = str(v).strip()
                                break
                else:
                    access_level = data_str
            except Exception:
                access_level = data_str if data_str else "Unknown"

            dev_logger.info(f"[DeepAlign] User Access Level: User={user}, Level={access_level}")
            self.lbl_laser_info.setText(f"Logged in as {user} ({access_level})")

        elif req_type == "GET_TEMP":
            # EUV Chamber 온도 파싱
            try:
                import json
                parsed = json.loads(data_str)
                if isinstance(parsed, dict) and "value" in parsed:
                    val = float(parsed["value"])
                else:
                    val = float(data_str)
                self.laser_states["temp"] = f"{val:.1f} °C"
            except Exception:
                self.laser_states["temp"] = data_str[:15]

        elif req_type == "GET_ON":
            # 레이저 전원 상태 파싱 (on / true / 1)
            is_on = ("true" in data_str.lower() or "on" in data_str.lower() or "1" == data_str)
            self.laser_states["on"] = "ON" if is_on else "OFF"
            
            # 레이저 온/오프 버튼 상태 및 스타일 자동 동기화
            self.btn_laser_toggle.blockSignals(True)
            self.btn_laser_toggle.setChecked(is_on)
            self.btn_laser_toggle.setText("🟢 LASER ON" if is_on else "🔴 LASER OFF")
            self.btn_laser_toggle.setStyleSheet(self._laser_btn_style(is_on))
            self.btn_laser_toggle.blockSignals(False)

        elif req_type == "GET_HF":
            # High Frequency Enable 상태 파싱 (true / on / enable / 1)
            is_hf = ("true" in data_str.lower() or "on" in data_str.lower() or "1" == data_str or "enable" in data_str.lower())
            self.laser_states["hf"] = "ENABLED" if is_hf else "DISABLED"

        elif req_type == "PUT_ON":
            # 제어 성공 시 상태 강제 싱크
            target_status = reply.property("target_status")
            is_on = (target_status == "on")
            self.laser_states["on"] = "ON" if is_on else "OFF"
            
            self.btn_laser_toggle.blockSignals(True)
            self.btn_laser_toggle.setChecked(is_on)
            self.btn_laser_toggle.setText("🟢 LASER ON" if is_on else "🔴 LASER OFF")
            self.btn_laser_toggle.setStyleSheet(self._laser_btn_style(is_on))
            self.btn_laser_toggle.blockSignals(False)

        self._update_laser_status_ui()

    def _update_laser_status_ui(self) -> None:
        """수집된 실시간 레이저 및 챔버 정보들을 정교하게 렌더링."""
        temp = self.laser_states.get("temp", "N/A")
        on_state = self.laser_states.get("on", "N/A")
        hf_state = self.laser_states.get("hf", "N/A")

        on_icon = "🟢" if on_state == "ON" else "🔴" if on_state == "OFF" else "⚪"
        hf_icon = "🟢" if hf_state == "ENABLED" else "🔴" if hf_state == "DISABLED" else "⚪"

        status_text = (
            f"🌡️ Target Temp : {temp}\n"
            f"{on_icon} Laser State : {on_state}\n"
            f"{hf_icon} High Freq  : {hf_state}"
        )
        self.lbl_laser_info.setText(status_text)

    def _laser_btn_style(self, is_on: bool) -> str:
        """레이저 상태에 따른 프리미엄 버튼 CSS 스타일."""
        if is_on:
            return """
                QPushButton {
                    background: #10b981; color: white; border: 1px solid #10b981;
                    border-radius: 4px; font-weight: bold; font-size: 12px; padding: 4px;
                }
                QPushButton:hover {
                    background: #059669;
                }
            """
        else:
            return """
                QPushButton {
                    background: #1e1e2f; color: #ef4444; border: 1px solid #ef4444;
                    border-radius: 4px; font-weight: bold; font-size: 12px; padding: 4px;
                }
                QPushButton:hover {
                    background: #ef444422;
                }
            """
