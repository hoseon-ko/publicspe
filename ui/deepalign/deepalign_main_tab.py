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
from PyQt6.QtCore import Qt, QThread, QTimer, QSettings, pyqtSignal, QSize, QEvent
from PyQt6.QtGui import QIcon, QPixmap, QImage, QPainter, QColor
from typing import Optional
import numpy as np

from ui.file_list_panel import SpeFileItem
from core.async_worker import SpeLoadWorker

from ui.deepalign.mirror_motor_panel import MirrorMotorPanel
from ui.deepalign.autofocus_panel import AutoFocusPanel
from ui.deepalign.acs_stage_panel import AcsStagePanel
from ui.motion.motion_tab import MotionTab
from ui.deepalign.deepalign_camera_controller import CameraControllerMixin
from ui.deepalign.deepalign_frame_pipeline import FramePipelineMixin
from ui.deepalign.deepalign_layout import LayoutBuilderMixin
from ui.deepalign.deepalign_styles import DeepAlignStylesMixin
from ui.deepalign.deepalign_workers import _AcquireWorker, _SnapWorker, _LiveWorker, _BgCaptureWorker
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
        self._snap_worker: Optional[_SnapWorker] = None
        self._live_worker_thread: Optional[QThread] = None
        self._live_worker: Optional[_LiveWorker] = None
        self._af_worker: Optional[AutoFocusWorker] = None

        # Acquire 상태 (dataclass로 통합 관리)
        self._acq = AcquireState()

        # Image processing
        self._proc_image: np.ndarray | None = None
        self._proc_mode: int = 1
        self._proc_enabled: bool = False

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

        # ── 기존 패널 인스턴스 생성 (단 1회) ──────────────────────────
        self.mirror_panel = MirrorMotorPanel()
        self.af_panel     = AutoFocusPanel()
        self.align_panel  = AcsStagePanel()
        self.motion_panel = MotionTab()
        self._settings = QSettings("SpeAnalyze", "DeepAlignTab")

        self._init_ui()
        self._bg_overlay = _BgProgressOverlay(self)
        self._init_frame_convert_worker()
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
        self.central_stack.setMaximumWidth(440)
        self.central_stack.setStyleSheet(
            "background-color: #0d121d; border-right: 1px solid #1e293b;"
        )

        self.central_stack.addWidget(self._create_cam_page())              # 0
        self.central_stack.addWidget(self._wrap_panel(self.mirror_panel))  # 1
        self.central_stack.addWidget(self._wrap_panel(self.af_panel))      # 2
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
        main_splitter.setSizes([340, 1240])
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
        self.af_panel.kimm_card.btn_connect.clicked.connect(self._on_af_kimm_connect_clicked)
        self.af_panel.kimm_card.btn_disconnect.clicked.connect(self._on_af_kimm_disconnect_clicked)

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
        self.btn_toggle_roi_sm.toggled.connect(self.dock_roi.setVisible)
        self.dock_plot.visibilityChanged.connect(
            lambda visible: self._sync_analysis_dock_toggle(self.dock_plot, self.btn_toggle_plot_sm, visible)
        )
        self.dock_hist.visibilityChanged.connect(
            lambda visible: self._sync_analysis_dock_toggle(self.dock_hist, self.btn_toggle_hist_sm, visible)
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
        self._sync_analysis_dock_toggle(self.dock_roi, self.btn_toggle_roi_sm, self.dock_roi.isVisible())

        # ROI Panel
        self.roi_panel.roi_selected.connect(self._on_roi_selected)
        self.roi_panel.roi_deleted.connect(self._on_roi_deleted)
        self.roi_panel.roi_goto.connect(self._on_roi_goto)

        # ── Settings persistence ──────────────────────────────────────
        self.cb_vendor.currentTextChanged.connect(self._save_settings)
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
        self.radio_proc_mode1.setEnabled(checked)
        self.radio_proc_mode2.setEnabled(checked)

    def _on_proc_mode_changed(self, mode_id: int) -> None:
        self._proc_mode = mode_id

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
        self.check_use_proc.setEnabled(has_img)
        if not has_img:
            self._proc_enabled = False
            self.check_use_proc.setChecked(False)
            self.radio_proc_mode1.setEnabled(False)
            self.radio_proc_mode2.setEnabled(False)
            self.lbl_proc_status.setText("No image loaded")
            self.lbl_proc_status.setStyleSheet(f"color: {C_TEXT_DEAD}; font-size: 11px; font-weight: bold;")
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

    def _save_settings(self):
        # Camera
        self._settings.setValue("camera/vendor",      self.cb_vendor.currentText())
        self._settings.setValue("camera/exposure_ms", float(self.spin_exposure.value()))
        self._settings.setValue("camera/fps",         float(self.spin_fps.value()))
        self._settings.setValue("camera/fps_lock",    bool(self.check_fps_lock.isChecked()))
        self._settings.setValue("camera/temp",        float(self.spin_temp.value()))
        self._settings.setValue("camera/adc_quality", self.cb_adc_quality.currentText())
        self._settings.setValue("camera/adc_speed",   self.cb_adc_speed.currentText())
        self._settings.setValue("camera/adc_gain",    self.cb_adc_gain.currentText())
        self._settings.setValue("camera/adc_bit",     self.cb_adc_bit.currentText())
        # Save
        self._settings.setValue("save/frame_to_save", int(self.spin_frame_to_save.value()))
        self._settings.setValue("save/folder",         self.edit_folder.text())
        self._settings.setValue("save/file_base",      self.edit_file_base.text())
        self._settings.setValue("save/inc_name",       bool(self.check_inc_name.isChecked()))
        self._settings.setValue("save/add_date",       bool(self.check_add_date.isChecked()))
        self._settings.setValue("save/add_time",       bool(self.check_add_time.isChecked()))
        self._settings.setValue("save/date_fmt",       self.cb_date_fmt.currentText())
        self._settings.setValue("save/time_fmt",       self.cb_time_fmt.currentText())
        self._settings.setValue("save/place",          self.cb_place.currentText())

    def _restore_settings(self):
        # Camera
        vendor    = str(self._settings.value("camera/vendor",      "Simulation"))
        exposure  = self._settings.value("camera/exposure_ms", 20.0,  type=float)
        fps       = self._settings.value("camera/fps",         30.0,  type=float)
        fps_lock  = self._settings.value("camera/fps_lock",    False, type=bool)
        temp      = self._settings.value("camera/temp",        -70.0, type=float)
        adc_qual  = str(self._settings.value("camera/adc_quality", ""))
        adc_spd   = str(self._settings.value("camera/adc_speed",   ""))
        adc_gain  = str(self._settings.value("camera/adc_gain",    ""))
        adc_bit   = str(self._settings.value("camera/adc_bit",     ""))
        # Save
        frame_to_save = self._settings.value("save/frame_to_save", 10,    type=int)
        save_folder   = str(self._settings.value("save/folder",    "Live_Captures"))
        file_base     = str(self._settings.value("save/file_base", "Capture"))
        inc_name      = self._settings.value("save/inc_name",  False, type=bool)
        add_date      = self._settings.value("save/add_date",  True,  type=bool)
        add_time      = self._settings.value("save/add_time",  True,  type=bool)
        date_fmt      = str(self._settings.value("save/date_fmt", "YYYY-Month-DD"))
        time_fmt      = str(self._settings.value("save/time_fmt", "hh:mm:ss (24h)"))
        place         = str(self._settings.value("save/place",    "Suffix"))

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

        self._update_save_control_state()
        self._update_save_preview()

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

    def set_kimm_ctrl(self, ctrl):
        self._kimm = ctrl

    def clear_kimm_ctrl(self):
        self._kimm = None

    def set_acs_ctrl(self, ctrl):
        """ACS 스테이지 컨트롤러를 align_panel에 주입한다."""
        self._acs = ctrl
        if hasattr(self, "align_panel"):
            self.align_panel.set_controller(ctrl)

    def clear_acs_ctrl(self):
        self._acs = None
        if hasattr(self, "align_panel"):
            self.align_panel.set_controller(None)

    def set_picos_ctrl(self, ctrl):
        self._picos = ctrl
        if hasattr(self, "mirror_panel") and ctrl is not None:
            self.mirror_panel.set_controller(ctrl)

    def clear_picos_ctrl(self):
        """Picomotor 연결 해제 시 mirror_panel 컨트롤러 초기화."""
        self._picos = None
        if hasattr(self, "mirror_panel"):
            self.mirror_panel.set_controller(None)

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

    def _on_af_kimm_connect_clicked(self):
        if not self._session_hub: return
        ip = self.af_panel.edit_kimm_ip.text().strip() or "192.168.1.100"
        try:
            self._session_hub.kimm_connect(ip, 5000)
            dev_logger.info(f"[DeepAlign] KIMM Z Connecting to {ip}...")
        except Exception as e:
            dev_logger.error(f"[DeepAlign] KIMM Connect error: {e}")

    def _on_af_kimm_disconnect_clicked(self):
        if not self._session_hub: return
        try:
            self._session_hub.kimm_disconnect()
            dev_logger.info("[DeepAlign] KIMM Z Disconnecting...")
        except Exception as e:
            dev_logger.error(f"[DeepAlign] KIMM Disconnect error: {e}")

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
            self.central_stack.setMaximumWidth(440)
            self.main_splitter.setSizes([340, 1240])
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
