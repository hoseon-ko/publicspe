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

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QSplitter
from PyQt6.QtCore import Qt, QThread, QTimer, QSettings, pyqtSignal
from typing import Optional

from ui.live.motor_panel import MotorPanel
from ui.live.autofocus_panel import AutoFocusPanel
from ui.live.acs_stage_panel import AcsStagePanel
from ui.motion.motion_tab import MotionTab
from ui.deepalign.deepalign_camera_controller import CameraControllerMixin
from ui.deepalign.deepalign_frame_pipeline import FramePipelineMixin
from ui.deepalign.deepalign_layout import LayoutBuilderMixin
from ui.deepalign.deepalign_styles import DeepAlignStylesMixin
from ui.deepalign.deepalign_workers import _AcquireWorker, _SnapWorker, _LiveWorker
from core.logger import dev_logger
from core.session.session_state import CameraConnectionState
from core.spe_writer import save_spe


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

        # Acquire 상태 (dataclass로 통합 관리)
        self._acq = AcquireState()

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
        self.mirror_panel = MotorPanel()
        self.af_panel     = AutoFocusPanel()
        self.align_panel  = AcsStagePanel()
        self.motion_panel = MotionTab()
        self._settings = QSettings("SpeAnalyze", "DeepAlignTab")

        self._init_ui()
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
        self.central_stack.addWidget(self._wrap_panel(self.align_panel))   # 3
        self.central_stack.addWidget(self._wrap_panel(self.motion_panel))  # 4
        self.central_stack.addWidget(self._create_analysis_page())         # 5

        # 3. 우측 작업영역: 도킹(QDockWidget) + 마스터바
        dock_workspace = self._create_docking_workspace()

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter = right_splitter
        right_splitter.setChildrenCollapsible(False)
        right_splitter.addWidget(dock_workspace)
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
        self.cam_viewer.roi_list_changed.connect(self._update_roi_list_from_viewer)

        # ── Global Analysis Panels Connection ────────────────────────
        self.cam_viewer.profile_updated.connect(
            lambda data, lbl: self.plot_panel.plot_line(data, lbl)
        )
        self.cam_viewer.multi_profile_updated.connect(
            lambda d1, d2: self.plot_panel.plot_two_lines(d1, d2, "X mean", "Y mean")
        )
        self.cam_viewer.histogram_updated.connect(self.hist_panel.plot_histogram)

        # ── Master bar — Mirror 탭 ────────────────────────────────────
        self.btn_mirror_zero_all.clicked.connect(self.mirror_panel.zero_all)
        self.btn_mirror_reset.clicked.connect(self.mirror_panel.reset_controller)
        self.btn_mirror_stop.clicked.connect(self.mirror_panel.stop_all)

        # ── Master bar — AutoFocus 탭 ─────────────────────────────────
        self.btn_af_run.clicked.connect(self.af_panel.run_af)
        self.btn_af_abort.clicked.connect(self.af_panel.abort_af)
        self.btn_af_set_z.clicked.connect(self.af_panel.set_z_base)

        # ── Master bar — Align 탭 ─────────────────────────────────────
        self.btn_align_enable.clicked.connect(self.align_panel.enable_all)
        self.btn_align_calc.clicked.connect(
            lambda: self._on_master_btn_not_implemented("Align / CALC KINEM.")
        )  # 키네마틱 계산 — 추후 구현
        self.btn_align_move.clicked.connect(self.align_panel.run)
        self.btn_align_stop.clicked.connect(self.align_panel.stop_all)

        # ── Master bar — Motion 탭 ────────────────────────────────────
        self.btn_motion_refresh.clicked.connect(self.motion_panel.refresh_positions)
        self.btn_motion_reconnect.clicked.connect(self.motion_panel.reconnect_all_devices)
        self.btn_motion_stop.clicked.connect(self.motion_panel.stop_all_motion)

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

    def bind_live_tab(self, live_tab):
        """Live 탭의 공유 하드웨어 패널을 DeepAlign에 연결한다.

        현재 DeepAlign은 카메라를 SessionHub를 통해 독립적으로 소유하므로
        LiveTab과의 카메라/프레임 직접 동기화는 수행하지 않는다.
        Motion 패널만 Live 탭 바인딩이 필요하므로 해당 경로만 유지한다.

        NOTE: colormap/range 동기화가 필요해지면 아래 패턴으로 복구한다:
            self._live_tab = live_tab
            self.cam_viewer.colormap_changed.connect(self._on_cmap_changed_sync)
            self.cam_viewer.range_changed.connect(live_tab.on_range_changed)
        """
        self.motion_panel.bind_live_tab(live_tab)
        # DeepAlign은 Hub 소유 카메라를 사용하므로 _live_tab 바인딩 불필요
        self._live_tab = None

    def bind_session_hub(self, session_hub):
        self._session_hub = session_hub
        if self._session_hub is None:
            self._set_camera_action_state(False)
            return
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
        _MOTION_PAGE_IDX = 4
        if idx == _MOTION_PAGE_IDX:
            self.central_stack.setMaximumWidth(16_777_215)
            self.right_splitter.setVisible(False)
            self.main_splitter.setSizes([1600, 0])
        else:
            self.right_splitter.setVisible(True)
            self.central_stack.setMaximumWidth(440)
            self.main_splitter.setSizes([340, 1240])
