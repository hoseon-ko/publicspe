"""
ui/scan/scan_tab.py
자동 스캔 탭 UI — 카메라 스냅 + 모터 이동 + 데이터 저장.

워커 로직은 ui/scan/scan_workers.py 참고.
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal, QSettings, QSize, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox,
    QComboBox, QLineEdit, QFileDialog,
    QProgressBar, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea,
    QCheckBox, QListWidget, QListWidgetItem,
)
from ui.widgets.auto_splitter import AutoSplitter
from PyQt6.QtGui import QIcon, QPixmap, QImage

from core.background_manager import BackgroundManager
from core.image_processor import ImageProcessor, TemporalMode
from ui.image_viewer import ImageViewer
from ui.plot_panel import PlotPanel
from ui.scan.scan_workers import _CalibWorker, _ScanWorker, _draw_centroid_cross
from ui.widgets.collapsible_section import CollapsibleSection
from ui.scan.mask_editor import MaskEditorDialog
from datetime import datetime
from theme.styles import (
    Fonts, Sizes,
    C_ACCENT, C_DANGER, C_WARN, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD,
    C_BG_DEEP, C_BG_DARK, C_BORDER,
    BTN_PRIMARY, BTN_DANGER, BTN_FLAT, BTN_SIM,
    SPIN_STYLE, COMBO_STYLE, EDIT_STYLE, TEXTEDIT_LOG, TABLE_STYLE,
    CHECKBOX_STYLE, SLIDER_STYLE, SPLITTER_V_STYLE,
    PROGRESS_STYLE, LIST_STYLE,
    grp_style, lbl, log_html,
)

# 하위 호환용 로컬 별칭
_F           = Fonts.UI
_FC          = Fonts.MONO
_FS_LBL      = Sizes.BTN     # scan은 레이블도 조금 크게
_FS_CTRL     = Sizes.BTN
_FS_BTN      = Sizes.BTN
_FS_GRP      = Sizes.CTRL
_FS_LOG      = Sizes.CTRL
_FS_TBL_HDR  = Sizes.BTN
_C_VAL       = C_TEXT
_C_LBL       = C_TEXT_DIM
_C_DIM       = C_TEXT_DEAD
_BTN_PRIMARY = BTN_PRIMARY
_BTN_DANGER  = BTN_DANGER
_SPIN_STYLE  = SPIN_STYLE
_COMBO_STYLE = COMBO_STYLE
_EDIT_STYLE  = EDIT_STYLE



def _sep_v() -> QWidget:
    """수직 구분선 위젯."""
    sep = QWidget()
    sep.setFixedSize(1, 20)
    sep.setStyleSheet("background:#1a3a60;")
    return sep

# ─────────────────────────────────────────────────────────────────────────────
# Scan 탭
# ─────────────────────────────────────────────────────────────────────────────

class ScanTab(QWidget):
    scan_starting    = pyqtSignal()   # → MainWindow → live_tab.stop_live()
    scan_done        = pyqtSignal()   # → MainWindow → live_tab.resume_live()
    log_message      = pyqtSignal(str)
    exposure_changed = pyqtSignal(float)  # 노출 동기화용

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam          = None
        self._motor_panel  = None
        self._sim_cam      = None
        self._sim_motor    = None
        self._real_cam     = None   # SIM 모드 진입 전 실제 카메라 백업
        self._real_motor   = None
        self._worker: Optional[_ScanWorker] = None
        self._calib_worker: Optional[_CalibWorker] = None
        self._scan_records: list = []
        self._image_list:   list = []   # 스텝별 raw ndarray 누적

        # 공유 ImageProcessor — 이진화/임계값 설정이 스캔 전반에 유지됨
        self._proc = ImageProcessor()
        self._proc.centroid_enabled = True

        # 무시 마스크 영역 목록 [(x1,y1,x2,y2), ...]
        self._mask_rects: list[tuple[int, int, int, int]] = []

        # BG는 BackgroundManager 싱글톤으로 공유
        self._bm = BackgroundManager.instance()
        self._bm.bg_changed.connect(self._on_bg_changed)
        self._scan_start_time: float = 0.0
        self._step_acq_timer = None  # set after build_ui

        self._plot_x:  list = []
        self._plot_cx: list = []
        self._plot_cy: list = []
        self.enable_profile_plot = True
        self._build_ui()
        self._restore_settings()

        self._step_acq_timer = QTimer()
        self._step_acq_timer.setInterval(50)
        self._step_acq_timer.timeout.connect(self._tick_acq_progress)
        self._acq_step_elapsed_ms: float = 0.0
        self._acq_step_duration_ms: float = 1000.0

    # ── Public API ────────────────────────────────────────────────────

    def set_shared_camera(self, cam):
        # 카메라가 바뀌면 이전 스캔 결과 초기화 (스캔 중이 아닐 때만)
        if self._cam is not cam:
            if not (self._worker and self._worker.isRunning()):
                self._reset_scan_results()
        self._cam = cam
        cam_name = type(cam).__name__.replace("Camera", "")
        self._lbl_cam.setText(f"📷 {cam_name}  ● CONNECTED")
        self._lbl_cam.setStyleSheet(f"color: #4ecdc4; font-family: '{_F}'; font-size: {_FS_LBL};")
        self.btn_start.setEnabled(True)
        self.btn_calibrate.setEnabled(True)
        try:
            exp = cam.get_exposure_ms()
            self.spin_exposure.setValue(exp)
        except Exception:
            pass

    def clear_shared_camera(self):
        if not (self._worker and self._worker.isRunning()):
            self._reset_scan_results()
        self._cam = None
        self._lbl_cam.setText("📷 카메라 없음")
        self._lbl_cam.setStyleSheet(f"color: #e94560; font-family: '{_F}'; font-size: {_FS_LBL};")
        self.btn_start.setEnabled(False)
        self.btn_calibrate.setEnabled(False)

    def _reset_scan_results(self):
        """스캔 결과(테이블·썸네일·이미지 리스트·플롯) 전체 초기화."""
        self._image_list.clear()
        self._scan_records.clear()
        self._plot_x.clear()
        self._plot_cx.clear()
        self._plot_cy.clear()
        self._frame_list.clear()
        self._table.setRowCount(0)
        self.progress_bar.setValue(0)
        self._lbl_progress.setText("—")
        self.plot_panel.clear()

    def set_motor_panel(self, motor_panel):
        """Live 탭의 MotorPanel 공유 — 위치 읽기 + 이동 명령."""
        self._motor_panel = motor_panel

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = AutoSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ── 좌측: 컨트롤 패널 ─────────────────────────────────────────
        ctrl_scroll = QScrollArea()
        ctrl_scroll.setWidgetResizable(True)
        ctrl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        ctrl_scroll.setStyleSheet("QScrollArea { border: none; background: #0a0f1e; }")
        ctrl_widget = QWidget()
        ctrl_widget.setStyleSheet("background: #0a0f1e;")
        ctrl_layout = QVBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(8, 8, 8, 8)
        ctrl_layout.setSpacing(8)
        ctrl_scroll.setWidget(ctrl_widget)
        ctrl_scroll.setMinimumWidth(320)

        splitter.setStyleSheet("QSplitter::handle { background-color: #4ecdc4; width: 4px; }")

        # 카메라 상태
        grp_cam = CollapsibleSection("CAMERA", accent=C_ACCENT)
        gc = grp_cam.content_layout()
        gc.setSpacing(5)
        self._lbl_cam = QLabel("📷 카메라 없음")
        self._lbl_cam.setStyleSheet(f"color: #e94560; font-family: '{_F}'; font-size: {_FS_LBL};")
        gc.addWidget(self._lbl_cam)

        self.btn_sim = QPushButton("▷  SIM MODE")
        self.btn_sim.setToolTip("실 하드웨어 없이 가상 카메라+모터로 동작 검증")
        self.btn_sim.setStyleSheet(BTN_SIM)
        self.btn_sim.setCheckable(True)
        self.btn_sim.clicked.connect(self._toggle_sim_mode)
        gc.addWidget(self.btn_sim)

        # 노출 시간
        exp_row = QHBoxLayout()
        lbl_exp = QLabel("노출(ms):")
        lbl_exp.setStyleSheet(f"color:{_C_LBL}; font-family:'{_F}'; font-size:{_FS_LBL};")
        lbl_exp.setFixedWidth(70)
        self.spin_exposure = QDoubleSpinBox()
        self.spin_exposure.setRange(0.01, 1_000_000.0)
        self.spin_exposure.setDecimals(2)
        self.spin_exposure.setValue(30.0)
        self.spin_exposure.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_exposure = QPushButton("APPLY")
        self.btn_apply_exposure.setStyleSheet(_BTN_PRIMARY)
        self.btn_apply_exposure.clicked.connect(self._apply_scan_exposure)
        exp_row.addWidget(lbl_exp)
        exp_row.addWidget(self.spin_exposure)
        exp_row.addWidget(self.btn_apply_exposure)
        gc.addLayout(exp_row)

        # 백그라운드
        bg_row = QHBoxLayout()
        self.btn_capture_bg = QPushButton("BG 획득")
        self.btn_capture_bg.setStyleSheet(_BTN_PRIMARY)
        self.btn_capture_bg.clicked.connect(self._capture_background)
        self.chk_bg_active = QCheckBox("BG 차감")
        self.chk_bg_active.setStyleSheet(f"QCheckBox {{ color:{_C_VAL}; font-family:'{_F}'; font-size:{_FS_LBL}; }}")
        self._lbl_bg_status = QLabel("없음")
        self._lbl_bg_status.setStyleSheet(f"color:{_C_DIM}; font-family:'{_FC}'; font-size:13px;")
        bg_row.addWidget(self.btn_capture_bg)
        bg_row.addWidget(self.chk_bg_active)
        bg_row.addWidget(self._lbl_bg_status)
        gc.addLayout(bg_row)

        ctrl_layout.addWidget(grp_cam)

        # 스캔 파라미터
        grp_scan = CollapsibleSection("SCAN PARAMETERS", accent=C_WARN)
        gs = grp_scan.content_layout()
        gs.setSpacing(6)

        def _row(label, widget):
            r = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(110)
            lbl.setStyleSheet(f"color: {_C_LBL}; font-family: '{_F}'; font-size: {_FS_LBL};")
            r.addWidget(lbl)
            r.addWidget(widget)
            return r

        # 모터 축 선택
        self.combo_motor = QComboBox()
        self.combo_motor.addItems(["M1", "M2", "M3", "M4"])
        self.combo_motor.setStyleSheet(_COMBO_STYLE)
        gs.addLayout(_row("이동 축:", self.combo_motor))

        # 스텝당 이동량
        self.spin_steps_move = QSpinBox()
        self.spin_steps_move.setRange(-999999, 999999)
        self.spin_steps_move.setValue(500)
        self.spin_steps_move.setStyleSheet(_SPIN_STYLE)
        gs.addLayout(_row("스텝/이동:", self.spin_steps_move))

        # 총 스텝 수
        self.spin_num_steps = QSpinBox()
        self.spin_num_steps.setRange(1, 10000)
        self.spin_num_steps.setValue(10)
        self.spin_num_steps.setStyleSheet(_SPIN_STYLE)
        gs.addLayout(_row("총 스텝 수:", self.spin_num_steps))

        # 정착 대기 시간
        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 60000)
        self.spin_settle.setValue(500)
        self.spin_settle.setSuffix(" ms")
        self.spin_settle.setStyleSheet(_SPIN_STYLE)
        gs.addLayout(_row("정착 대기:", self.spin_settle))

        # 버퍼 플러시 스냅 수 (카메라 하드웨어 버퍼에 남은 낡은 프레임 폐기)
        self.spin_flush = QSpinBox()
        self.spin_flush.setRange(0, 10)
        self.spin_flush.setValue(0)
        self.spin_flush.setToolTip(
            "모터 정착 후 측정 전 버릴 프레임 수\n"
            "카메라 하드웨어 버퍼에 잔류한 낡은 프레임 제거용\n"
            "(대부분 0으로 충분, 하드웨어 버퍼가 큰 경우 1~2)"
        )
        self.spin_flush.setStyleSheet(_SPIN_STYLE)
        gs.addLayout(_row("버퍼 플러시:", self.spin_flush))

        ctrl_layout.addWidget(grp_scan)

        # 저장 경로
        grp_save = CollapsibleSection("SAVE", accent="#a080ff")
        gsv = grp_save.content_layout()

        self.edit_scan_name = QLineEdit("Scan")
        self.edit_scan_name.setPlaceholderText("스캔 이름")
        self.edit_scan_name.setStyleSheet(_EDIT_STYLE)
        gsv.addWidget(self.edit_scan_name)

        dir_row = QHBoxLayout()
        self.edit_save_dir = QLineEdit("Scan_Data")
        self.edit_save_dir.setStyleSheet(_EDIT_STYLE)
        btn_browse = QPushButton("…")
        btn_browse.setFixedWidth(28)
        btn_browse.setStyleSheet(
            "QPushButton { background:#0d1e38; color:#a0b0d0; border:1px solid #1a3060;"
            "border-radius:3px; font-size:12px; } QPushButton:hover { color:#4ecdc4; }"
        )
        btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.edit_save_dir)
        dir_row.addWidget(btn_browse)
        gsv.addLayout(dir_row)

        ctrl_layout.addWidget(grp_save)

        # 시작 / 정지
        self.btn_start = QPushButton("▶  START SCAN")
        self.btn_start.setStyleSheet(_BTN_PRIMARY)
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._start_scan)

        self.btn_stop = QPushButton("■  STOP")
        self.btn_stop.setStyleSheet(_BTN_DANGER)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_scan)

        ctrl_layout.addWidget(self.btn_start)
        ctrl_layout.addWidget(self.btn_stop)

        # 진행바
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background:#080e1e; border:1px solid #0f3460; border-radius:4px;
                color:#4ecdc4; font-family:'{_F}'; font-size:{_FS_LOG}; }}
            QProgressBar::chunk {{ background:#0d2820; border-radius:3px; }}
        """)
        ctrl_layout.addWidget(self.progress_bar)

        # 현재 스텝 획득 진행바
        self._acq_bar = QProgressBar()
        self._acq_bar.setRange(0, 100)
        self._acq_bar.setValue(0)
        self._acq_bar.setTextVisible(False)
        self._acq_bar.setFixedHeight(6)
        self._acq_bar.setStyleSheet("""
            QProgressBar { background:#080e1e; border:1px solid #0f3460; border-radius:2px; }
            QProgressBar::chunk { background:#ffe66d; border-radius:2px; }
        """)
        ctrl_layout.addWidget(self._acq_bar)

        self._lbl_progress = QLabel("—")
        self._lbl_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_progress.setStyleSheet(
            "color: #4a6a8a; font-family: 'Courier New'; font-size: 10px;"
        )
        ctrl_layout.addWidget(self._lbl_progress)

        # ── 프레임 분석 ────────────────────────────────────────────────
        grp_frame = CollapsibleSection("FRAME ANALYSIS", accent="#ff9f43")
        gf = grp_frame.content_layout()
        gf.setSpacing(5)

        # 최대 보관 프레임 수
        self.spin_max_frames = QSpinBox()
        self.spin_max_frames.setRange(1, 9999)
        self.spin_max_frames.setValue(200)
        self.spin_max_frames.setSuffix(" frames")
        self.spin_max_frames.setStyleSheet(_SPIN_STYLE)
        self.spin_max_frames.setToolTip("메모리 보호: 초과 시 가장 오래된 프레임부터 삭제")
        gf.addLayout(_row("최대 보관:", self.spin_max_frames))


        # Frame A / B 선택
        ab_row = QHBoxLayout()
        for lbl_text, attr in (("A:", "spin_frame_a"), ("B:", "spin_frame_b")):
            l = QLabel(lbl_text)
            l.setFixedWidth(14)
            l.setStyleSheet(f"color:{_C_LBL}; font-family:'{_F}'; font-size:{_FS_LBL};")
            sp = QSpinBox()
            sp.setRange(0, 9999)
            sp.setValue(0)
            sp.setStyleSheet(_SPIN_STYLE)
            setattr(self, attr, sp)
            ab_row.addWidget(l)
            ab_row.addWidget(sp)
        gf.addLayout(ab_row)

        # 표시 버튼 (checkable — 마지막으로 표시된 모드 강조)
        _VIEW_BTN = f"""
            QPushButton {{
                background: #0d2820; color: #4ecdc4;
                border: 1px solid #4ecdc4; border-radius: 4px;
                font-family: '{_F}'; font-weight: bold;
                font-size: {_FS_BTN}; padding: 7px 14px;
            }}
            QPushButton:hover  {{ background: #1a4838; }}
            QPushButton:checked {{
                background: #0d3828; color: #ffe66d;
                border: 2px solid #ffe66d;
            }}
            QPushButton:disabled {{ color: #1a2840; background: #080e1e; border-color: #0a1828; }}
        """
        btn_row_f1 = QHBoxLayout()
        self.btn_show_a = QPushButton("Show A")
        self.btn_show_b = QPushButton("Show B")
        btn_row_f2 = QHBoxLayout()
        self.btn_diff    = QPushButton("A − B")
        self.btn_absdiff = QPushButton("|A − B|")

        self._view_btns = (self.btn_show_a, self.btn_show_b,
                           self.btn_diff, self.btn_absdiff)
        for btn in self._view_btns:
            btn.setCheckable(True)
            btn.setStyleSheet(_VIEW_BTN)
        for btn in (self.btn_show_a, self.btn_show_b):
            btn_row_f1.addWidget(btn)
        for btn in (self.btn_diff, self.btn_absdiff):
            btn_row_f2.addWidget(btn)
        gf.addLayout(btn_row_f1)
        gf.addLayout(btn_row_f2)

        self.btn_show_a.clicked.connect(lambda: self._show_frame_view(
            self.btn_show_a, lambda: self._show_frame_idx(self.spin_frame_a.value())))
        self.btn_show_b.clicked.connect(lambda: self._show_frame_view(
            self.btn_show_b, lambda: self._show_frame_idx(self.spin_frame_b.value())))
        self.btn_diff.clicked.connect(lambda: self._show_frame_view(
            self.btn_diff, self._show_diff))
        self.btn_absdiff.clicked.connect(lambda: self._show_frame_view(
            self.btn_absdiff, self._show_abs_diff))
        ctrl_layout.addWidget(grp_frame)

        # ── 무시 마스크 ────────────────────────────────────────────────
        grp_mask = CollapsibleSection("IGNORE MASK", accent=C_DANGER)
        gm = grp_mask.content_layout()
        gm.setSpacing(5)

        self._lbl_mask_count = QLabel("비활성")
        self._lbl_mask_count.setStyleSheet(
            f"color:#4a6a8a; font-family:'{_FC}'; font-size:13px;"
        )
        gm.addWidget(self._lbl_mask_count)

        mask_btn_row = QHBoxLayout()
        self.btn_edit_mask = QPushButton("영역 편집")
        self.btn_clear_mask = QPushButton("초기화")
        self.btn_edit_mask.setStyleSheet(_BTN_PRIMARY)
        self.btn_clear_mask.setStyleSheet(_BTN_DANGER)
        self.btn_edit_mask.clicked.connect(self._edit_mask)
        self.btn_clear_mask.clicked.connect(self._clear_mask)
        mask_btn_row.addWidget(self.btn_edit_mask)
        mask_btn_row.addWidget(self.btn_clear_mask)
        gm.addLayout(mask_btn_row)

        ctrl_layout.addWidget(grp_mask)

        # ── 캘리브레이션 ───────────────────────────────────────────────
        grp_calib = CollapsibleSection("CALIBRATION", accent="#fd79a8")
        gcal = grp_calib.content_layout()
        gcal.setSpacing(5)

        # 캘리브레이션 스텝 수
        self.spin_calib_steps = QSpinBox()
        self.spin_calib_steps.setRange(10, 999999)
        self.spin_calib_steps.setValue(1000)
        self.spin_calib_steps.setStyleSheet(_SPIN_STYLE)
        gcal.addLayout(_row("캘리브 스텝:", self.spin_calib_steps))

        # 대상 모터 선택 (체크박스)
        chk_row = QHBoxLayout()
        self._calib_chk = {}
        for mn in (1, 2, 3):
            chk = QCheckBox(f"M{mn}")
            chk.setChecked(True)
            chk.setStyleSheet(
                f"QCheckBox {{ color:{_C_VAL}; font-family:'{_F}'; font-size:{_FS_LBL}; }}"
            )
            self._calib_chk[mn] = chk
            chk_row.addWidget(chk)
        gcal.addLayout(chk_row)

        self.btn_calibrate = QPushButton("⚙  CALIBRATE")
        self.btn_calibrate.setStyleSheet(_BTN_PRIMARY)
        self.btn_calibrate.setEnabled(False)
        self.btn_calibrate.clicked.connect(self._start_calibration)
        gcal.addWidget(self.btn_calibrate)

        ctrl_layout.addWidget(grp_calib)

        ctrl_layout.addStretch()
        splitter.addWidget(ctrl_scroll)

        # ── 중앙+우측: 이미지 + 플롯 ──────────────────────────────────
        center_right = QSplitter(Qt.Orientation.Horizontal)

        # 이미지 뷰어 + 옵션 바 컨테이너
        img_container = QWidget()
        img_container.setStyleSheet("background: #080e1e;")
        img_vbox = QVBoxLayout(img_container)
        img_vbox.setContentsMargins(0, 0, 0, 0)
        img_vbox.setSpacing(0)

        # ── 이미지 옵션 바 ──────────────────────────────────────────
        img_opt_bar = QWidget()
        img_opt_bar.setFixedHeight(36)
        img_opt_bar.setStyleSheet(
            "background:#0a1428; border-bottom:1px solid #0f3460;"
        )
        opt_layout = QHBoxLayout(img_opt_bar)
        opt_layout.setContentsMargins(8, 4, 8, 4)
        opt_layout.setSpacing(10)

        # 원본 / 이진화 토글
        self.btn_view_raw = QPushButton("원본")
        self.btn_view_bin = QPushButton("이진화")
        _view_btn_style = f"""
            QPushButton {{
                background:#0f1729; color:{_C_LBL};
                border:1px solid #1a3a60; border-radius:3px;
                font-family:'{_F}'; font-size:13px; font-weight:bold;
                padding:2px 10px; min-width:52px;
            }}
            QPushButton:checked {{
                background:#0d2820; color:#4ecdc4; border-color:#4ecdc4;
            }}
            QPushButton:hover {{ color:{_C_VAL}; }}
        """
        for b in (self.btn_view_raw, self.btn_view_bin):
            b.setCheckable(True)
            b.setStyleSheet(_view_btn_style)
        self.btn_view_raw.setChecked(True)
        self.btn_view_raw.clicked.connect(lambda: self._set_view_mode(False))
        self.btn_view_bin.clicked.connect(lambda: self._set_view_mode(True))
        opt_layout.addWidget(self.btn_view_raw)
        opt_layout.addWidget(self.btn_view_bin)

        opt_layout.addWidget(_sep_v())

        # 임계값 스핀박스 (raw 픽셀값 기준 0~65535)
        lbl_t = QLabel("임계값:")
        lbl_t.setStyleSheet(f"color:{_C_LBL}; font-family:'{_F}'; font-size:13px;")
        self.slider_thresh = QSpinBox()
        self.slider_thresh.setRange(0, 65535)
        self.slider_thresh.setValue(1000)
        self.slider_thresh.setSingleStep(100)
        self.slider_thresh.setFixedWidth(90)
        self.slider_thresh.setStyleSheet(f"""
            QSpinBox {{ background:#0f1e38; color:{_C_VAL}; border:1px solid #1a3460;
                border-radius:3px; padding:1px 4px;
                font-family:'{_FC}'; font-size:12px; }}
            QSpinBox::up-button, QSpinBox::down-button {{ width:14px; background:#1a3060; }}
        """)
        self.slider_thresh.valueChanged.connect(self._on_thresh_changed)
        opt_layout.addWidget(lbl_t)
        opt_layout.addWidget(self.slider_thresh)

        opt_layout.addWidget(_sep_v())

        # 중심점 표시 토글
        self.chk_centroid_marker = QCheckBox("중심점 표시")
        self.chk_centroid_marker.setChecked(True)
        self.chk_centroid_marker.setStyleSheet(
            f"QCheckBox {{ color:{_C_VAL}; font-family:'{_F}'; font-size:13px; }}"
        )
        opt_layout.addWidget(self.chk_centroid_marker)

        opt_layout.addStretch()

        img_vbox.addWidget(img_opt_bar)

        self.image_viewer = ImageViewer()
        img_vbox.addWidget(self.image_viewer)

        center_right.addWidget(img_container)

        # 우측: 세로 스플리터 (드래그로 높낮이 조절)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setStyleSheet("""
            QSplitter::handle:vertical {
                background: #1a3a60; height: 4px; margin: 1px 0;
            }
            QSplitter::handle:vertical:hover { background: #4ecdc4; }
        """)

        # ── 패널 1: 썸네일 리스트 ─────────────────────────────────────
        frames_widget = QWidget()
        frames_widget.setStyleSheet("background: #0a0f1e;")
        frames_layout = QVBoxLayout(frames_widget)
        frames_layout.setContentsMargins(6, 4, 6, 4)
        frames_layout.setSpacing(4)

        lbl_frames = QLabel("CAPTURED FRAMES")
        lbl_frames.setStyleSheet(
            f"color:#4ecdc4; font-family:'{_F}'; font-size:16px; "
            "font-weight:bold; letter-spacing:2px; padding:0;"
        )
        frames_layout.addWidget(lbl_frames)

        self._frame_list = QListWidget()
        self._frame_list.setIconSize(QSize(80, 60))
        self._frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self._frame_list.setWrapping(False)
        self._frame_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._frame_list.setMinimumHeight(60)
        self._frame_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._frame_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frame_list.setStyleSheet("""
            QListWidget { background:#080e1e; border:1px solid #0f3460; color:#c0d0ff; }
            QListWidget::item { padding:2px; border:1px solid #0f2040; }
            QListWidget::item:selected { background:#1a3a60; border:1px solid #4ecdc4; }
        """)
        self._frame_list.currentRowChanged.connect(self._on_frame_list_select)
        frames_layout.addWidget(self._frame_list)
        right_splitter.addWidget(frames_widget)

        # ── 패널 2: 플롯 ──────────────────────────────────────────────
        self.plot_panel = PlotPanel("Centroid X/Y vs Motor Position")
        self.plot_panel.setMinimumHeight(100)
        right_splitter.addWidget(self.plot_panel)

        # ── 패널 3: 로그 ──────────────────────────────────────────────
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMinimumHeight(40)
        self.log_display.setStyleSheet(
            f"QTextEdit {{ background:#080e1e; border:1px solid #0f3460;"
            f"color:#00cc88; font-family:'Courier New'; font-size:{_FS_LOG}; }}"
        )
        right_splitter.addWidget(self.log_display)

        # ── 패널 4: 결과 테이블 ───────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(11)
        self._table.setHorizontalHeaderLabels(
            ["Step", "M1", "M2", "M3", "M4", "CentX", "CentY", "σX", "σY", "SNR", "SPE"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMinimumHeight(40)
        self._table.setStyleSheet(f"""
            QTableWidget {{ background:#080e1e; gridline-color:#0f3460;
                color:#c0d0ff; font-family:'{_FC}'; font-size:{_FS_LOG}; border:none; text-align:center; }}
            QHeaderView::section {{ background:#0f1729; color:#4ecdc4;
                border:1px solid #0f3460; font-family:'{_F}'; font-size:{_FS_TBL_HDR};
                font-weight:bold; padding:4px 2px; text-align:center; }}
            QTableWidget::item:selected {{ background:#1a3a60; }}
        """)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right_splitter.addWidget(self._table)

        # 초기 높이 비율: 썸네일 120 / 플롯 280 / 로그 120 / 테이블 200
        right_splitter.setSizes([120, 280, 120, 200])
        self._right_splitter = right_splitter   # QSettings 저장용

        center_right.addWidget(right_splitter)
        center_right.setSizes([700, 400])

        splitter.addWidget(center_right)
        splitter.setSizes([290, 1110])

    # ── 노출 제어 ─────────────────────────────────────────────────────

    def _apply_scan_exposure(self):
        if self._cam is None:
            return
        ms = self.spin_exposure.value()
        try:
            actual = self._cam.set_exposure_ms(ms)
            self.spin_exposure.setValue(actual)
            self._log(f"노출 설정: {actual:.2f} ms")
            self.exposure_changed.emit(actual)
        except Exception as e:
            self._log(f"⚠️ 노출 설정 실패: {e}")

    def set_exposure_ui(self, ms: float):
        """다른 탭에서 노출값 변경 시 UI만 업데이트."""
        self.spin_exposure.blockSignals(True)
        self.spin_exposure.setValue(ms)
        self.spin_exposure.blockSignals(False)

    # ── 백그라운드 제어 ───────────────────────────────────────────────

    def _on_bg_changed(self, has_bg: bool):
        """BackgroundManager BG 변경 시 UI 동기화."""
        if has_bg:
            self._lbl_bg_status.setText(self._bm.status_text())
            self._lbl_bg_status.setStyleSheet(
                f"color: #4ecdc4; font-family: '{_FC}'; font-size: 13px;"
            )
            self.chk_bg_active.setEnabled(True)
            self.chk_bg_active.setChecked(True)
        else:
            self._lbl_bg_status.setText("없음")
            self._lbl_bg_status.setStyleSheet(
                f"color: {C_TEXT_DIM}; font-family: '{_FC}'; font-size: 13px;"
            )
            self.chk_bg_active.setEnabled(False)
            self.chk_bg_active.setChecked(False)

    def _capture_background(self):
        if self._cam is None:
            self._log("❌ 카메라 연결 필요")
            return
        try:
            raw = np.asarray(self._cam.snap())
            self._bm.set_frame(raw)              # ← BackgroundManager에 등록
            self._log(f"📸 배경 획득: {raw.shape[1]}×{raw.shape[0]}")
        except Exception as e:
            self._log(f"⚠️ 배경 획득 실패: {e}")

    # ── 스텝 획득 진행 타이머 ─────────────────────────────────────────

    def _tick_acq_progress(self):
        self._acq_step_elapsed_ms += 50.0
        pct = min(99, int(self._acq_step_elapsed_ms / max(self._acq_step_duration_ms, 1) * 100))
        self._acq_bar.setValue(pct)

    def _on_step_started(self, idx: int, estimated_ms: float):
        self._acq_step_elapsed_ms = 0.0
        self._acq_step_duration_ms = max(estimated_ms, 100.0)
        self._acq_bar.setValue(0)
        self._step_acq_timer.start()

    # ── 컨트롤 잠금 ──────────────────────────────────────────────────

    def _set_controls_locked(self, locked: bool):
        """동작 중 모든 파라미터/시작 버튼 잠금, 정지만 활성."""
        idle = not locked
        cam_ok = self._cam is not None
        motor_ok = self._motor_panel is not None and self._motor_panel.is_connected

        self.btn_start.setEnabled(idle and cam_ok)
        self.btn_stop.setEnabled(locked and (self._worker is not None))
        self.btn_calibrate.setEnabled(idle and cam_ok and motor_ok)
        self.btn_sim.setEnabled(idle)

        # 파라미터 위젯
        for w in (
            self.combo_motor,
            self.spin_steps_move, self.spin_num_steps, self.spin_settle,
            self.spin_max_frames, self.spin_calib_steps,
            self.spin_frame_a, self.spin_frame_b,
            self.edit_scan_name, self.edit_save_dir,
            self.btn_show_a, self.btn_show_b,
            self.btn_diff, self.btn_absdiff,
            self.spin_exposure, self.btn_apply_exposure,
            self.btn_capture_bg,
        ):
            w.setEnabled(idle)
        for chk in self._calib_chk.values():
            chk.setEnabled(idle)

    # ── 이미지 표시 옵션 ─────────────────────────────────────────────

    def _set_view_mode(self, binary: bool):
        """원본/이진화 토글 — 설정 변경 후 현재 뷰를 즉시 재렌더링."""
        self._proc.show_binary = binary
        self.btn_view_raw.setChecked(not binary)
        self.btn_view_bin.setChecked(binary)
        self.slider_thresh.setEnabled(binary)
        self._refresh_current_view()

    def _on_thresh_changed(self, val: int):
        self._proc.bin_threshold = float(val)
        self._refresh_current_view()

    def _refresh_current_view(self):
        """현재 active view 버튼에 맞게 이미지뷰어를 재렌더링한다."""
        if not self._image_list:
            return
        if self.btn_show_a.isChecked():
            self._show_frame_idx(self.spin_frame_a.value())
        elif self.btn_show_b.isChecked():
            self._show_frame_idx(self.spin_frame_b.value())
        elif self.btn_diff.isChecked():
            self._show_diff()
        elif self.btn_absdiff.isChecked():
            self._show_abs_diff()
        else:
            # 아무것도 선택 안 된 경우 — 마지막 프레임 표시
            self._show_frame_idx(len(self._image_list) - 1)

    # ── 이미지 뷰어에 RGB + 중심점 마커 표시 ─────────────────────────

    def _display_result(self, result, fit: bool = False):
        """ProcessResult → image_viewer (컬러맵 유지) + 센트로이드 씬 오버레이."""
        disp = result.display
        # set_image_first/set_image 경로를 타서 _current_cmap / range 설정을 그대로 유지
        if fit:
            self.image_viewer.set_image_first(disp)
        else:
            self.image_viewer.set_image(disp)

        # 센트로이드는 픽셀에 굽지 않고 씬 오버레이로 표시 → 컬러맵 재적용 시에도 유지
        if self.chk_centroid_marker.isChecked() and result.has_centroid:
            self.image_viewer.set_centroid_overlay(result.centroid_x, result.centroid_y)
        else:
            self.image_viewer.clear_centroid_overlay()

    # ── 스캔 제어 ─────────────────────────────────────────────────────

    def _start_scan(self):
        if self._cam is None:
            self._log("❌ 카메라 연결 필요")
            return
        if self._worker and self._worker.isRunning():
            self._log("⚠️ 이미 스캔 중")
            return
        if self._calib_worker and self._calib_worker.isRunning():
            self._log("❌ 캘리브레이션 진행 중 — 완료 후 시작")
            return

        motor_num   = int(self.combo_motor.currentText()[1])
        steps_move  = self.spin_steps_move.value()
        num_steps   = self.spin_num_steps.value()
        settle_ms   = self.spin_settle.value()
        save_dir    = self.edit_save_dir.text().strip() or "Scan_Data"
        scan_name   = self.edit_scan_name.text().strip() or "Scan"

        if self._motor_panel and not self._motor_panel.is_connected:
            self._log("⚠️ Picomotor 연결 안 됨 — 위치 기록 없이 진행")

        self.scan_starting.emit()
        self._set_controls_locked(True)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(num_steps)
        self._table.setRowCount(0)
        self._frame_list.clear()
        self._scan_records.clear()
        self._image_list.clear()
        self._plot_x.clear()
        self._plot_cx.clear()
        self._plot_cy.clear()

        import time
        params = {
            "motor_num":         motor_num,
            "steps_move":        steps_move,
            "num_steps":         num_steps,
            "settle_ms":         settle_ms,
            "save_dir":          save_dir,
            "scan_name":         scan_name,
            "flush_snaps":       self.spin_flush.value(),
            "ignore_mask_rects": list(self._mask_rects),
            "exposure_ms":       self.spin_exposure.value(),
        }
        # 마스크는 워커가 첫 스냅 후 크기에 맞게 빌드하므로 여기서 리셋
        self._proc.ignore_mask = None
        self._scan_start_time = time.monotonic()
        self._worker = _ScanWorker(self._cam, self._motor_panel, params, proc=self._proc)
        self._worker.step_done.connect(self._on_step_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._log)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.step_started.connect(self._on_step_started)
        self._worker.start()
        self._log(f"▶ 스캔 시작 — M{motor_num} × {num_steps} steps ({steps_move} step/move)")

    def _stop_scan(self):
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self.btn_stop.setEnabled(False)  # 중복 요청 방지
        self._log("■ 정지 요청...")

    # ── 워커 콜백 ─────────────────────────────────────────────────────

    def _on_step_done(self, idx: int, result, positions: list, spe_path: str):
        # 이미지 리스트 누적 (상한 초과 시 가장 오래된 것 제거)
        max_frames = self.spin_max_frames.value()
        pos_snapshot = [p if p is not None else 0 for p in positions]
        self._image_list.append((idx, result.raw.copy(), pos_snapshot))
        if len(self._image_list) > max_frames:
            self._image_list.pop(0)
            self._frame_list.takeItem(0)
            self._log(f"⚠️ 프레임 상한 {max_frames}개 — 가장 오래된 RAM 복사본 제거 (SPE는 디스크에 유지)")

        # 프레임 스핀박스 최대값 갱신
        n = len(self._image_list) - 1
        self.spin_frame_a.setMaximum(n)
        self.spin_frame_b.setMaximum(n)

        # 썸네일 리스트 추가
        self._append_thumbnail(result.display, idx)

        # 이미지 표시 (중심점 오버레이 포함)
        self._display_result(result, fit=(idx == 0))

        # 테이블 추가
        row = self._table.rowCount()
        self._table.insertRow(row)
        p = [p if p is not None else 0 for p in positions]
        cx = f"{result.centroid_x:.1f}" if result.centroid_x is not None else "—"
        cy = f"{result.centroid_y:.1f}" if result.centroid_y is not None else "—"
        vals = [
            str(idx + 1),
            str(p[0]), str(p[1]), str(p[2]), str(p[3]),
            cx, cy,
            f"{result.beam_sigma_x:.1f}",
            f"{result.beam_sigma_y:.1f}",
            f"{result.snr:.2f}",
            os.path.basename(spe_path),
        ]
        for col, v in enumerate(vals):
            item = QTableWidgetItem(v)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, col, item)
        self._table.scrollToBottom()

        # 플롯 업데이트 — centroid None이면 0으로 채워 길이 항상 일치
        motor_num = int(self.combo_motor.currentText()[1])
        self._plot_x.append(p[motor_num - 1])
        self._plot_cx.append(result.centroid_x if result.centroid_x is not None else 0.0)
        self._plot_cy.append(result.centroid_y if result.centroid_y is not None else 0.0)

        if self.enable_profile_plot:
            self.plot_panel.plot_two_lines(
                np.array(self._plot_cx),
                np.array(self._plot_cy),
                "Centroid X",
                "Centroid Y",
            )

    def _on_progress(self, current: int, total: int):
        import time
        self._step_acq_timer.stop()
        self._acq_bar.setValue(100)
        self.progress_bar.setValue(current)
        elapsed = time.monotonic() - self._scan_start_time
        if current > 0:
            avg = elapsed / current
            remaining = avg * (total - current)
            self._lbl_progress.setText(
                f"Step {current}/{total}  |  경과 {elapsed:.0f}s  |  남은 ~{remaining:.0f}s"
            )
        else:
            self._lbl_progress.setText(f"Step {current}/{total}")

    def _on_scan_finished(self, csv_path: str):
        self._step_acq_timer.stop()
        self._acq_bar.setValue(0)
        self._worker = None
        self._set_controls_locked(False)
        # B 스핀박스를 마지막 프레임 인덱스로 자동 세팅
        if self._image_list:
            last = len(self._image_list) - 1
            self.spin_frame_b.setValue(last)
            self.spin_frame_a.setValue(0)
            # 마지막 프레임(B)을 자동 표시하고 Show B 버튼 active로
            self._show_frame_view(
                self.btn_show_b,
                lambda: self._show_frame_idx(last),
            )
        if csv_path:
            self._log(f"✅ 스캔 완료 — CSV: {csv_path}")
        else:
            self._log("✅ 스캔 완료 (데이터 없음)")
        self.scan_done.emit()

    def _on_scan_error(self, msg: str):
        self._step_acq_timer.stop()
        self._acq_bar.setValue(0)
        self._log(f"❌ {msg}")
        self._worker = None
        self._set_controls_locked(False)
        self.scan_done.emit()

    # ── 썸네일 ───────────────────────────────────────────────────────

    def _append_thumbnail(self, display: np.ndarray, step_idx: int):
        """display(uint8 2D/3D)를 80×60 썸네일로 QListWidget에 추가."""
        disp = display
        if disp.ndim == 2:
            disp = np.stack([disp, disp, disp], axis=-1)
        h, w = disp.shape[:2]
        thumb_w, thumb_h = 80, 60
        # 비율 유지 리사이즈
        scale = min(thumb_w / w, thumb_h / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        try:
            import cv2
            small = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
        except ImportError:
            small = disp[::max(1, h // nh), ::max(1, w // nw)][:nh, :nw]

        # 검은 배경 캔버스에 중앙 배치
        canvas = np.zeros((thumb_h, thumb_w, 3), dtype=np.uint8)
        y0 = (thumb_h - nh) // 2
        x0 = (thumb_w - nw) // 2
        canvas[y0:y0+nh, x0:x0+nw] = small[:, :, :3]

        img = QImage(canvas.tobytes(), thumb_w, thumb_h, thumb_w * 3, QImage.Format.Format_RGB888)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(img)), f"#{step_idx+1}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._frame_list.addItem(item)
        self._frame_list.scrollToItem(item)

    def _on_frame_list_select(self, row: int):
        """리스트에서 프레임 선택 → 이미지뷰어 + 스핀박스 동기화."""
        if row < 0 or row >= len(self._image_list):
            return
        self._show_frame_idx(row)
        # 스핀박스 B를 마지막 선택에 맞춤 (A는 항상 직전)
        self.spin_frame_b.setValue(row)
        if row > 0:
            self.spin_frame_a.setValue(row - 1)

    # ── 프레임 분석 ───────────────────────────────────────────────────

    def _show_frame_view(self, active_btn, action):
        """view 버튼 중 active_btn만 checked 상태로 만들고 action 실행."""
        for btn in self._view_btns:
            btn.setChecked(btn is active_btn)
        action()

    def _show_frame_idx(self, idx: int):
        if not self._image_list:
            self._log("⚠️ 저장된 프레임 없음")
            return
        idx = max(0, min(idx, len(self._image_list) - 1))
        step_i, raw, pos = self._image_list[idx]
        result = self._proc.process(raw)   # 현재 이진화/임계값 설정 반영
        self._display_result(result, fit=False)
        cx = f"{result.centroid_x:.1f}" if result.centroid_x is not None else "N/A"
        cy = f"{result.centroid_y:.1f}" if result.centroid_y is not None else "N/A"
        self._log(
            f"🖼 Step#{step_i+1}  Centroid=({cx},{cy})  "
            f"M1={pos[0]}  M2={pos[1]}  M3={pos[2]}  M4={pos[3]}"
        )

    def _show_diff(self):
        self._render_diff(absolute=False)

    def _show_abs_diff(self):
        self._render_diff(absolute=True)

    def _render_diff(self, absolute: bool):
        if len(self._image_list) < 2:
            self._log("⚠️ 비교할 프레임 2개 이상 필요")
            return
        a_idx = max(0, min(self.spin_frame_a.value(), len(self._image_list) - 1))
        b_idx = max(0, min(self.spin_frame_b.value(), len(self._image_list) - 1))
        if a_idx == b_idx:
            self._log("⚠️ A와 B가 같은 프레임")
            return

        try:
            step_a, raw_a, pos_a = self._image_list[a_idx]
            step_b, raw_b, pos_b = self._image_list[b_idx]
            a = raw_a.astype(np.float32)
            b = raw_b.astype(np.float32)
            dp = [pb - pa for pa, pb in zip(pos_a, pos_b)]
            self._log(
                f"A=Step#{step_a+1} → B=Step#{step_b+1}  "
                f"ΔM1={dp[0]:+d}  ΔM2={dp[1]:+d}  ΔM3={dp[2]:+d}  ΔM4={dp[3]:+d}"
            )
            diff = a - b

            if absolute:
                # |A-B|: hot 컬러맵 (0=검정 → 빨강 → 노랑 → 흰색)
                arr = np.abs(diff)
                vmax = float(arr.max()) or 1.0
                f = arr / vmax                
                r_ch = np.clip(f * 3.0,       0, 1)
                g_ch = np.clip(f * 3.0 - 1.0, 0, 1)
                b_ch = np.clip(f * 3.0 - 2.0, 0, 1)
                rgb = np.stack(
                    [(r_ch * 255).astype(np.uint8),
                     (g_ch * 255).astype(np.uint8),
                     (b_ch * 255).astype(np.uint8)],
                    axis=-1
                )
                self._log(f"|A-B|  max={arr.max():.1f}  mean={arr.mean():.2f}")
            else:
                # A-B: diverging — 양수(A>B)→빨강, 음수(B>A)→파랑
                peak = float(max(abs(diff.min()), abs(diff.max()))) or 1.0
                norm = diff / peak  # -1 ~ +1
                r_ch = np.clip( norm * 255, 0, 255).astype(np.uint8)
                b_ch = np.clip(-norm * 255, 0, 255).astype(np.uint8)
                g_ch = np.zeros_like(r_ch)
                rgb = np.stack([r_ch, g_ch, b_ch], axis=-1)
                self._log(
                    f"A-B  min={diff.min():.1f}  max={diff.max():.1f}  "
                    f"mean={diff.mean():.2f}"
                )

            self.image_viewer.set_live_frame(
                np.ascontiguousarray(rgb), fit=False
            )
        except Exception as e:
            self._log(f"❌ diff 렌더링 오류: {e}")

    # ── 무시 마스크 ───────────────────────────────────────────────────

    def _edit_mask(self):
        """스냅 또는 마지막 프레임을 불러와 MaskEditorDialog를 열고 결과를 저장."""
        img = None
        if self._cam is not None:
            try:
                img = np.asarray(self._cam.snap())
            except Exception as e:
                self._log(f"⚠️ 스냅 실패 — 마지막 프레임으로 대체: {e}")
        if img is None and self._image_list:
            _, img, _ = self._image_list[-1]
        if img is None:
            self._log("⚠️ 편집할 이미지 없음 — 먼저 스캔하거나 카메라 연결")
            return
        dlg = MaskEditorDialog(img, self._mask_rects, parent=self)
        if dlg.exec():
            self._mask_rects = dlg.get_rects()
            self._update_mask_label()
            n = len(self._mask_rects)
            self._log(f"✅ 마스크 {n}개 영역 설정" if n else "마스크 초기화")

    def _clear_mask(self):
        self._mask_rects.clear()
        self._proc.ignore_mask = None
        self._update_mask_label()
        self._log("마스크 초기화")

    def _update_mask_label(self):
        n = len(self._mask_rects)
        if n > 0:
            self._lbl_mask_count.setText(f"{n}개 영역 활성")
            self._lbl_mask_count.setStyleSheet(
                f"color:#ff7675; font-family:'{_FC}'; font-size:13px; font-weight:bold;"
            )
        else:
            self._lbl_mask_count.setText("비활성")
            self._lbl_mask_count.setStyleSheet(
                f"color:#4a6a8a; font-family:'{_FC}'; font-size:13px;"
            )

    # ── 캘리브레이션 ──────────────────────────────────────────────────

    def _start_calibration(self):
        if self._cam is None:
            self._log("❌ 카메라 연결 필요")
            return
        if self._motor_panel is None or not self._motor_panel.is_connected:
            self._log("❌ 모터 연결 필요")
            return
        if self._calib_worker and self._calib_worker.isRunning():
            self._log("⚠️ 캘리브레이션 이미 진행 중")
            return
        if self._worker and self._worker.isRunning():
            self._log("❌ 스캔 진행 중 — 완료 후 시작")
            return

        motors = [mn for mn, chk in self._calib_chk.items() if chk.isChecked()]
        if not motors:
            self._log("⚠️ 캘리브레이션할 모터 선택 필요")
            return

        params = {
            "calib_steps": self.spin_calib_steps.value(),
            "settle_ms":   self.spin_settle.value(),
            "motors":      motors,
        }
        total_steps = 1 + len(motors) * 2
        self.progress_bar.setMaximum(total_steps)
        self.progress_bar.setValue(0)
        self._lbl_progress.setText(f"0 / {total_steps}")

        self.scan_starting.emit()   # Live 스트림 정지 (Picam 리소스 충돌 방지)
        self._calib_worker = _CalibWorker(self._cam, self._motor_panel, params)
        self._calib_worker.log_message.connect(self._log)
        self._calib_worker.progress.connect(self._on_progress)
        self._calib_worker.result_ready.connect(self._on_calib_result)
        self._set_controls_locked(True)
        self._calib_worker.start()
        self._log(f"⚙ 캘리브레이션 시작 — M{motors}  ±{params['calib_steps']} steps")

    def _on_calib_result(self, results: dict):
        self._calib_worker = None
        self._set_controls_locked(False)
        self.scan_done.emit()   # Live 재개
        self._log("── 캘리브레이션 결과 ──")
        for motor_num, res in results.items():
            parts = [f"M{motor_num}:"]
            if "fwd" in res:
                f = res["fwd"]
                parts.append(f"FWD Δ({f['dx']:+.2f},{f['dy']:+.2f}) {f['mag']:.2f}px {f['angle']:.1f}°")
            if "bwd" in res:
                b = res["bwd"]
                parts.append(f"BWD Δ({b['dx']:+.2f},{b['dy']:+.2f}) {b['mag']:.2f}px {b['angle']:.1f}°")
            if "weight_adj" in res:
                parts.append(f"adj={res['weight_adj']:.4f}")
            self._log("  " + "  |  ".join(parts))

    # ── 유틸 ─────────────────────────────────────────────────────────

    def _toggle_sim_mode(self, checked: bool):
        if (self._worker and self._worker.isRunning()) or \
           (self._calib_worker and self._calib_worker.isRunning()):
            self._log("❌ 동작 중 SIM 모드 전환 불가")
            self.btn_sim.setChecked(not checked)  # 토글 되돌리기
            return
        if checked:
            # 실제 카메라/모터 보관 (SIM 해제 시 복원)
            self._real_cam   = self._cam
            self._real_motor = self._motor_panel
            from core.simulator import SimCamera, SimMotorPanel
            self._sim_cam   = SimCamera()
            self._sim_motor = SimMotorPanel(self._sim_cam)
            self.set_shared_camera(self._sim_cam)
            self._motor_panel = self._sim_motor
            self._lbl_cam.setText("🟡 SIM  ● Gaussian Beam  512×512")
            self._lbl_cam.setStyleSheet(
                f"color: #ffe66d; font-family: '{_F}'; font-size: {_FS_LBL};"
            )
            self.btn_sim.setText("■  SIM OFF")
            self._log("🟡 SIM MODE 활성 — 가상 카메라 + M1/M2/M3 가중치 비대칭 모터")
        else:
            self._sim_cam   = None
            self._sim_motor = None
            # 저장된 실제 카메라 복원
            real_cam   = getattr(self, "_real_cam",   None)
            real_motor = getattr(self, "_real_motor", None)
            self._real_cam   = None
            self._real_motor = None
            if real_cam is not None:
                self.set_shared_camera(real_cam)
            else:
                self.clear_shared_camera()
            self._motor_panel = real_motor
            self.btn_sim.setText("▷  SIM MODE")
            self._log("⬛ SIM MODE 해제" + ("" if real_cam is None else " — 실제 카메라 복원"))

    def _browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.edit_save_dir.text())
        if path:
            self.edit_save_dir.setText(path)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_display.append(log_html(msg, ts))

    def _restore_settings(self):
        s = QSettings("SpeAnalyze", "ScanTab")
        self.combo_motor.setCurrentText(s.value("motor", "M1"))
        self.spin_steps_move.setValue(int(s.value("steps_move", 500)))
        self.spin_num_steps.setValue(int(s.value("num_steps", 10)))
        self.spin_settle.setValue(int(s.value("settle_ms", 500)))
        self.edit_save_dir.setText(s.value("save_dir", "Scan_Data"))
        self.edit_scan_name.setText(s.value("scan_name", "Scan"))
        raw = s.value("right_splitter_sizes")
        if raw:
            try:
                self._right_splitter.setSizes([int(x) for x in raw])
            except Exception:
                pass
        thresh = int(s.value("bin_threshold", 1000))
        self.slider_thresh.setValue(thresh)
        # _proc 동기화
        self._proc.bin_threshold = float(thresh)
        self.slider_thresh.setEnabled(False)  # 원본 모드 기본

    def _save_settings(self):
        s = QSettings("SpeAnalyze", "ScanTab")
        s.setValue("motor",      self.combo_motor.currentText())
        s.setValue("steps_move", self.spin_steps_move.value())
        s.setValue("num_steps",  self.spin_num_steps.value())
        s.setValue("settle_ms",  self.spin_settle.value())
        s.setValue("save_dir",   self.edit_save_dir.text())
        s.setValue("scan_name",  self.edit_scan_name.text())
        s.setValue("right_splitter_sizes", self._right_splitter.sizes())
        s.setValue("bin_threshold", self.slider_thresh.value())
        s.sync()

    def cleanup(self):
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(2000)
        if self._calib_worker and self._calib_worker.isRunning():
            self._calib_worker.request_stop()
            self._calib_worker.wait(2000)
