"""
ui/live/acs_stage_panel.py
ACS SPiiPlus 6축 키네마틱 스테이지 제어 패널 — SpeAnalyze 다크 테마.

기능:
  - Ethernet IP/Port 입력 + 시뮬레이터 모드
  - 6축 실시간 위치 표시 (300 ms 폴링)
  - 축별 Enable / Disable + 조그
  - 전체 Enable All / Disable All / Stop All
  - 6DOF 키네마틱 입력 (Trans X/Y/Z mm + Rotate Rx/Ry/Rz mrad) → CalPos → 이동
  - Dry Run 모드
"""

from __future__ import annotations

import threading
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QGroupBox, QGridLayout,
    QDoubleSpinBox, QSpinBox, QCheckBox, QTextEdit,
)
from PyQt6.QtCore import Qt, QSettings, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent

from core.motor.acs_stage import AcsStageController, AXIS_LABELS, DEFAULT_PORT
from core.motor.kinematic_calc import KinematicCalc, is_available as kinematic_available
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_BG_DEEP, C_BG_DARK, C_TEXT, C_TEXT_DIM,
    Fonts, Sizes, BTN_SMALL, SPIN_STYLE, EDIT_STYLE, CHECKBOX_STYLE, grp_style, lbl
)

# ── 하위 호환용 로컬 별칭 ──────────────────────────────────────────────
_FC       = Fonts.MONO
_FS_TITLE = Sizes.TITLE
_FS_BTN   = Sizes.BTN
_FS_CTRL  = Sizes.CTRL
_FS_LOG   = Sizes.LOG
_FS_SMALL = Sizes.SMALL
_LBL      = lbl()
_SPIN     = SPIN_STYLE
_BTN_S    = BTN_SMALL

# 색상/스타일 별칭
_C_BG     = C_BG_DEEP
_C_DIM    = C_TEXT_DIM
_C_WARN   = C_WARN
_C_ACCENT = C_ACCENT
_C_DANGER = C_DANGER
_C_BORDER = C_BORDER


# ── 설정 키 ──────────────────────────────────────────────────────────

_SETTINGS_KEY_IP  = "acs/ip"
_SETTINGS_KEY_PORT = "acs/port"
_SETTINGS_KEY_DRY  = "acs/dry_run"


def _btn(color: str) -> str:
    return f"""
        QPushButton {{
            background: transparent; color: {color};
            border: 1px solid {color}; border-radius: 3px;
            font-family: '{_FC}'; font-size: 14px;
            font-weight: bold; padding: 2px 6px;
        }}
        QPushButton:hover {{ background: {color}22; }}
        QPushButton:disabled {{ color: #304060; border-color: #1a2840; }}
    """


def _spin_style(bg: str | None = None) -> str:
    bg_color = bg if bg else _C_BG
    return f"""
        QSpinBox, QDoubleSpinBox {{
            background: {bg_color}; border: 1px solid {_C_BORDER};
            color: #c0d0ff; border-radius: 3px;
            font-family: '{_FC}'; font-size: 14px; padding: 1px 4px;
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0px; }}
    """


class _DofSpinBox(QDoubleSpinBox):
    """Ctrl+C / Ctrl+V / Ctrl+A 를 내부 QLineEdit으로 명시 전달하는 스핀박스.

    QDoubleSpinBox는 내부적으로 QLineEdit을 사용하지만 PyQt6에서
    클립보드 단축키가 전달되지 않는 경우가 있어 keyPressEvent에서 직접 처리한다.
    """

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            key = event.key()
            if key == Qt.Key.Key_C:
                self.lineEdit().copy()
                return
            if key == Qt.Key.Key_V:
                self.lineEdit().paste()
                return
            if key == Qt.Key.Key_A:
                self.lineEdit().selectAll()
                return
        super().keyPressEvent(event)


class _AxisRow:
    """6축 그리드에서 한 행을 구성하는 위젯 묶음."""

    def __init__(self, idx: int, grid: QGridLayout, ctrl_ref: list, move_btns: list, log_cb):
        """
        grid 에 위젯을 직접 추가한다.
        ctrl_ref[0] 에 AcsStageController 인스턴스가 들어온다 (mutable container).
        move_btns 에 조그 버튼들을 append 한다 (연결 시 활성화).
        log_cb 로깅 콜백 함수.
        """
        self.idx = idx
        self._ctrl_ref = ctrl_ref
        self._log = log_cb

        row = idx + 1

        # 레이블
        lbl_obj = QLabel(AXIS_LABELS[idx])
        lbl_obj.setStyleSheet(lbl(C_ACCENT, bold=True))
        lbl_obj.setFixedWidth(40)
        grid.addWidget(lbl_obj, row, 0)

        # 위치 표시
        self.lbl_pos = QLabel("---")
        self.lbl_pos.setStyleSheet(lbl(C_TEXT, mono=True))
        self.lbl_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.lbl_pos, row, 1)

        # 모터 상태 인디케이터
        self.lbl_state = QLabel("●")
        self.lbl_state.setStyleSheet("color:#304060; font-size:13px;")
        self.lbl_state.setFixedWidth(26)
        grid.addWidget(self.lbl_state, row, 2)

        # 1. 서보 상태 (Servo Status)
        self.lbl_servo = QLabel("OFF")
        self.lbl_servo.setFixedWidth(50)
        self.lbl_servo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_servo.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; border: 1px solid #334155; border-radius: 4px;")
        grid.addWidget(self.lbl_servo, row, 3)

        # 2. 이동 완료 상태 (Move Done / In-Position LED)
        self.lbl_done = QLabel("●")
        self.lbl_done.setFixedWidth(40)
        self.lbl_done.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_done.setStyleSheet("color:#1e293b; font-size:16px;")
        self.lbl_done.setToolTip("MOVE DONE (In-Position)")
        grid.addWidget(self.lbl_done, row, 4)

    def update_position(self, pos: float):
        self.lbl_pos.setText(f"{pos:+.4f} mm")

    def update_state(self, state: dict | bool):
        """서보 상태 및 In-Position 상태 업데이트.
        state 가 dict 일 경우 {'enabled': bool, 'in_pos': bool} 정보를 사용.
        bool 일 경우 enabled 상태로만 간주 (하위 호환).
        """
        if isinstance(state, dict):
            enabled = state.get("enabled", False)
            in_pos  = state.get("in_pos", False)
        else:
            enabled = bool(state)
            in_pos  = False
            
        # Servo Status 표시
        if enabled:
            self.lbl_servo.setText("ENABLED")
            self.lbl_servo.setStyleSheet("color: #14b8a6; font-size: 11px; font-weight: bold; border: 1px solid #14b8a6; border-radius: 4px; background: rgba(20,184,166,0.1);")
        else:
            self.lbl_servo.setText("OFF")
            self.lbl_servo.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; border: 1px solid #334155; border-radius: 4px;")

        # Move Done (In-Position) LED 표시
        color = "#10b981" if in_pos else "#304060" # 완료 시 녹색, 이동 중 어두운 색
        self.lbl_done.setStyleSheet(f"color:{color}; font-size:16px;")

    def _ctrl(self) -> AcsStageController | None:
        return self._ctrl_ref[0]


class _KinematicMoveWorker(QThread):
    """13-step servo ON → move → servo OFF 시퀀스를 GUI 스레드 밖에서 실행."""

    log      = pyqtSignal(str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    _SERVO_ON_MS   = 1000   # 서보 ON 확인 대기
    _SETTLE_MS     = 500    # In-Position 후 정착 대기
    _INPOS_TIMEOUT = 30000  # WaitMotionEnd 타임아웃 (ms)

    def __init__(self, ctrl: AcsStageController, targets: np.ndarray, 
                 limits_plus: np.ndarray, limits_minus: np.ndarray,
                 settle_ms: int = 500, dry: bool = False):
        super().__init__()
        self._ctrl = ctrl
        self._targets = targets
        self._limits_plus = limits_plus
        self._limits_minus = limits_minus
        self._settle_ms = settle_ms
        self._dry = dry

    def run(self):
        try:
            if self._dry:
                self.log.emit("[DRY RUN] 서보 ON → 이동 → 서보 OFF 시뮬레이션")
                self.msleep(300)
                self.log.emit("[DRY RUN] KINEMATIC MOVE 완료")
                self.finished.emit()
                return

            # ① Servo ON
            self.log.emit("[ACS] ① Servo ON")
            self._ctrl.enable_all()
 
            # ② 서보 ON 확인 대기 1s
            self.log.emit("[ACS] ② Servo ON 확인 대기 (1 s)")
            self.msleep(self._SERVO_ON_MS)
 
            # ③ 전축 이동 명령 (동시 발사 — wait=False)
            motor_names = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
            self.log.emit("[ACS] ③ Move 명령 전송 (전축 동시)")
            for i, (name, target, p_lim, n_lim) in enumerate(zip(motor_names, self._targets, self._limits_plus, self._limits_minus)):
                # 사용자가 요청한 형식: [ACS] [AXIS] POS [TARGET] [PLIMIT] [NLIMIT]
                self.log.emit(f"   [ACS] [{name}] POS: {target:+.4f} [PLIM: {p_lim:+.4f}, NLIM: {n_lim:+.4f}]")
                self._ctrl.move_to(i, float(target), wait=False)
 
            # ④ In-Position 완료 대기 (IsCheckDoneStateAll)
            self.log.emit("[ACS] ④ In-Position 완료 대기")
            self._ctrl.wait_in_position_all(timeout_ms=self._INPOS_TIMEOUT)
 
            # ⑤ Settle Wait: 이동 완료 후 진동 억제를 위한 대기
            self.log.emit(f"[ACS] ⑤ Settle Time 대기 ({self._settle_ms} ms)")
            self.msleep(self._settle_ms)
 
            # ⑥ Servo OFF (락 걸기)
            self.log.emit("[ACS] ⑥ Servo OFF (락 걸기)")
            self._ctrl.disable_all()
 
            # ⑦ 완료
            self.log.emit("[KINEMATICS] ⑦ KINEMATIC MOVE 완료")
            self.finished.emit()

        except Exception as e:
            self.error.emit(str(e))


class AcsStagePanel(QWidget):
    """ACS SPiiPlus 6축 스테이지 제어 패널."""

    log_message     = pyqtSignal(str)
    acs_connected   = pyqtSignal(object)   # AcsStageController
    acs_disconnected = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # _ctrl_ref: _AxisRow 인스턴스들이 컨트롤러를 공유하기 위한 mutable 컨테이너.
        # 연결/해제 시 [0] 슬롯만 갱신하면 모든 _AxisRow가 즉시 새 ctrl을 본다.
        self._ctrl_ref: list[AcsStageController | None] = [None]
        self._move_btns: list[QPushButton] = []
        self._axis_rows: list[_AxisRow] = []
        self._settings = QSettings("SpeAnalyze", "MainWindow")
        self._calc = KinematicCalc()
        self._lbl_cur_dof: dict[str, QLabel] = {}

        self._kin_worker: _KinematicMoveWorker | None = None

        # 5분 자동 서보 OFF 타이머 (마지막 이동 완료 후 대기 중 안전 해제)
        self._auto_disable_timer = QTimer(self)
        self._auto_disable_timer.setSingleShot(True)
        self._auto_disable_timer.setInterval(5 * 60 * 1000)
        self._auto_disable_timer.timeout.connect(self._on_auto_disable)

        # 실시간 포즈 계산용 (마지막 계산된 결과 저장)
        self._last_actual_dof: Optional[np.ndarray] = None

        self._build_ui()
        self._load_settings()

    # ── UI 구성 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # 1. Connection
        self.sec_conn = CollapsibleSection("CONNECTION", accent=C_ACCENT)
        self._build_conn_group(self.sec_conn.content_layout())
        root.addWidget(self.sec_conn)

        # 2. Axes Status
        self.sec_axes = CollapsibleSection("6-AXIS POSITIONS", accent=C_DANGER)
        self._build_axis_group(self.sec_axes.content_layout())
        root.addWidget(self.sec_axes)

        # 3. Global Control
        self.sec_global = CollapsibleSection("GLOBAL CONTROL", accent=C_ACCENT)
        self._build_global_group(self.sec_global.content_layout())
        root.addWidget(self.sec_global)

        # 4. Kinematic Move
        self.sec_kin = CollapsibleSection("6DOF KINEMATIC MOVE", accent="#aa7acc")
        self._build_kinematic_group(self.sec_kin.content_layout())
        root.addWidget(self.sec_kin)

        root.addStretch()

    def _build_conn_group(self, lay: QVBoxLayout):
        lay.setSpacing(6)
        lay.setContentsMargins(4, 4, 4, 4)

        # IP
        row_ip = QHBoxLayout()
        lbl_ip = QLabel("IP")
        lbl_ip.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_ip.setFixedWidth(50)
        self.edit_ip = QLineEdit()
        self.edit_ip.setPlaceholderText("10.0.0.100")
        self.edit_ip.setStyleSheet(EDIT_STYLE)
        row_ip.addWidget(lbl_ip)
        row_ip.addWidget(self.edit_ip)
        lay.addLayout(row_ip)

        # Port
        row_port = QHBoxLayout()
        lbl_port = QLabel("PORT")
        lbl_port.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_port.setFixedWidth(50)
        self.edit_port = QLineEdit(str(DEFAULT_PORT))
        self.edit_port.setFixedWidth(80)
        self.edit_port.setStyleSheet(EDIT_STYLE)
        self.check_sim = QCheckBox("SIM")
        self.check_sim.setStyleSheet(CHECKBOX_STYLE)
        row_port.addWidget(lbl_port)
        row_port.addWidget(self.edit_port)
        row_port.addStretch()
        row_port.addWidget(self.check_sim)
        lay.addLayout(row_port)

        # 버튼
        row_btn = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setStyleSheet(BTN_SMALL)
        self.btn_connect.clicked.connect(self._on_connect)

        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.clicked.connect(self._on_disconnect)

        row_btn.addWidget(self.btn_connect)
        row_btn.addWidget(self.btn_disconnect)
        lay.addLayout(row_btn)

        # 상태
        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        lay.addWidget(self.lbl_status)


    def _build_axis_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        # 6축 그리드
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)

        # 헤더 (조작 버튼 제거 반영)
        headers = ["Axis", "Position", "", "Servo", "Done"]
        for col, txt in enumerate(headers):
            lbl_hdr = QLabel(txt)
            lbl_hdr.setStyleSheet(lbl(C_TEXT_DIM, size="12px", mono=True))
            if col == 1:
                lbl_hdr.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(lbl_hdr, 0, col)

        for i in range(6):
            row = _AxisRow(i, grid, self._ctrl_ref, self._move_btns, self._log)
            self._axis_rows.append(row)
        lay.addLayout(grid)


    def _build_global_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        row.setSpacing(4)

        self.btn_en_all  = QPushButton("ENABLE ALL")
        self.btn_dis_all = QPushButton("DISABLE ALL")
        self.btn_stop    = QPushButton("STOP ALL")

        self.btn_en_all .setStyleSheet(_btn(C_ACCENT))
        self.btn_dis_all.setStyleSheet(_btn(C_WARN))
        self.btn_stop   .setStyleSheet(_btn(C_DANGER))

        for b in (self.btn_en_all, self.btn_dis_all, self.btn_stop):
            b.setEnabled(False)
            self._move_btns.append(b)

        self.btn_en_all .clicked.connect(self._on_enable_all)
        self.btn_dis_all.clicked.connect(self._on_disable_all)
        self.btn_stop   .clicked.connect(self._on_stop_all)

        row.addWidget(self.btn_en_all)
        row.addWidget(self.btn_dis_all)
        row.addWidget(self.btn_stop)
        lay.addLayout(row)


    def _build_kinematic_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)

        unavail = not kinematic_available()


        # 상단 공통 Step 제거 (각 행으로 분산)
        # ─────────────────────────────────────────────────────────────
        
        # Trans / Rotate 입력 그리드 + Jog 버튼
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setContentsMargins(4, 4, 4, 4)

        # 헤더 추가
        headers = ["DOF", "ACTUAL (GET)", "TARGET (SET)", "STEP", "", ""]
        for col, txt in enumerate(headers):
            h_lbl = QLabel(txt)
            h_lbl.setStyleSheet(lbl(C_TEXT_DIM, size="10px", mono=True))
            if col in [1, 2, 3]:
                h_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(h_lbl, 0, col)

        labels = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        self._dof_spins: list[QDoubleSpinBox] = []
        self._dof_step_spins: list[QDoubleSpinBox] = [] # 개별 스텝용
        self._lbl_cur_dof: dict[str, QLabel] = {}

        for i, lbl_txt in enumerate(labels):
            grid_row = i + 1
            
            # 1. DOF 이름
            lbl_obj = QLabel(lbl_txt)
            lbl_obj.setStyleSheet(lbl(C_ACCENT, bold=True))
            lbl_obj.setFixedWidth(24)
            grid.addWidget(lbl_obj, grid_row, 0)

            # 2. ACTUAL (GET) 레이블
            val_get = QLabel("---")
            val_get.setFixedWidth(85)
            val_get.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_get.setStyleSheet(lbl(C_WARN, bold=True, mono=True))
            grid.addWidget(val_get, grid_row, 1)
            self._lbl_cur_dof[lbl_txt] = val_get

            # 3. TARGET (SET) SpinBox
            spin = _DofSpinBox()
            spin.setRange(-500.0, 500.0)
            spin.setValue(0.0)
            spin.setDecimals(4)
            spin.setFixedWidth(85)
            spin.setStyleSheet(_spin_style())
            spin.setEnabled(not unavail)
            grid.addWidget(spin, grid_row, 2)
            self._dof_spins.append(spin)
            
            # 4. STEP SpinBox (개별 스텝)
            step_spin = QDoubleSpinBox()
            step_spin.setRange(0.0001, 100.0)
            step_spin.setValue(0.1) # 기본값
            step_spin.setDecimals(4)
            step_spin.setFixedWidth(75)
            step_spin.setStyleSheet(_spin_style("#1e293b"))
            step_spin.setEnabled(not unavail)
            grid.addWidget(step_spin, grid_row, 3)
            self._dof_step_spins.append(step_spin)

            # 5. Minus Button
            btn_m = QPushButton("−")
            btn_m.setFixedWidth(28)
            btn_m.setStyleSheet(_btn(C_DANGER))
            btn_m.setEnabled(not unavail)
            btn_m.clicked.connect(lambda _, idx=i: self._on_kin_jog(idx, -1))
            self._move_btns.append(btn_m)
            grid.addWidget(btn_m, grid_row, 4)

            # 6. Plus Button
            btn_p = QPushButton("+")
            btn_p.setFixedWidth(28)
            btn_p.setStyleSheet(_btn(C_ACCENT))
            btn_p.setEnabled(not unavail)
            btn_p.clicked.connect(lambda _, idx=i: self._on_kin_jog(idx, 1))
            self._move_btns.append(btn_p)
            grid.addWidget(btn_p, grid_row, 5)

        lay.addLayout(grid)

        # [Sync Button] Get -> Set (그리드 바로 아래 배치)
        self.btn_sync_get_to_set = QPushButton("SYNC ACTUAL (GET) → TARGET (SET)")
        self.btn_sync_get_to_set.setToolTip("현재 실제 위치를 목표 입력칸으로 복사합니다.")
        self.btn_sync_get_to_set.setStyleSheet(_btn(C_WARN).replace("transparent", "#1a1510"))
        self.btn_sync_get_to_set.setFixedHeight(28)
        self.btn_sync_get_to_set.setEnabled(False)
        self.btn_sync_get_to_set.clicked.connect(self._on_sync_get_to_set)
        lay.addWidget(self.btn_sync_get_to_set)

        # CALC + MOVE 버튼 (하단)
        row_btn = QHBoxLayout()
        self.btn_kin_calc = QPushButton("CALC KINEMATICS")
        self.btn_kin_calc.setStyleSheet(_btn("#aa7acc"))
        self.btn_kin_calc.setEnabled(not unavail)
        self.btn_kin_calc.clicked.connect(self._on_kin_calc)

        self.btn_kin_move = QPushButton("EXECUTE MOVE")
        self.btn_kin_move.setStyleSheet(_btn(C_ACCENT))
        self.btn_kin_move.setEnabled(False)
        self._move_btns.append(self.btn_kin_move)
        self.btn_kin_move.clicked.connect(self._on_kin_move)

        row_btn.addWidget(self.btn_kin_calc, 1)
        row_btn.addWidget(self.btn_kin_move, 1)
        lay.addLayout(row_btn)

        # CalPos 결과 표시
        self.kin_result = QTextEdit()
        self.kin_result.setReadOnly(True)
        self.kin_result.setFixedHeight(110)
        self.kin_result.setStyleSheet(f"""
            QTextEdit {{
                background:{_C_BG}; border:1px solid {_C_BORDER};
                color:#c0d0ff; font-family:'{_FC}'; font-size:13px;
            }}
        """)
        if unavail:
            self.kin_result.setPlainText("⚠ AlignStageAlgorithm 로드 실패\n(AlignStageAlgorithm.py 확인)")
        lay.addWidget(self.kin_result)

        # 마지막 계산된 calPos 보관
        self._last_cal_pos = None



        # SETTINGS (Dry Run, Settle Time)
        lay.setContentsMargins(4, 8, 4, 8)
        
        row_set = QHBoxLayout()
        self.check_dry = QCheckBox("DRY RUN")
        self.check_dry.setStyleSheet(
            f"QCheckBox {{ color:{_C_WARN}; font-family:'{Fonts.UI}'; font-size:13px; font-weight:bold; }}"
        )
        self.check_dry.toggled.connect(self._on_dry_run)
        
        row_set.addWidget(self.check_dry)
        row_set.addStretch()
        
        lbl_set = QLabel("Settle(ms):")
        lbl_set.setStyleSheet(f"color:{_C_DIM}; font-family:'{Fonts.UI}'; font-size:12px;")
        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 10000)
        self.spin_settle.setValue(500)
        self.spin_settle.setFixedWidth(70)
        self.spin_settle.setStyleSheet(_spin_style())
        self.spin_settle.valueChanged.connect(self._save_settings)
        
        row_set.addWidget(lbl_set)
        row_set.addWidget(self.spin_settle)
        lay.addLayout(row_set)



    # ── 연결 / 해제 ────────────────────────────────────────────────────

    def _on_connect(self):
        ip       = self.edit_ip.text().strip()
        port_str = self.edit_port.text().strip()
        use_sim  = self.check_sim.isChecked()

        if not use_sim and not ip:
            self._log("[ACS] IP를 입력하세요")
            return
 
        self._save_settings()
        ctrl = AcsStageController()
        ctrl.dry_run = self.check_dry.isChecked()
 
        try:
            if use_sim:
                self._log("[ACS] 시뮬레이터 연결 시도...")
                ctrl.connect_simulator()
            else:
                try:
                    port = int(port_str)
                except ValueError:
                    self._log("[ACS] Port는 정수여야 합니다")
                    return
                self._log(f"[ACS] 연결 시도 → {ip}:{port}")
                ctrl.connect(ip, port)
 
            self._ctrl_ref[0] = ctrl
            label = "SIMULATOR" if use_sim else f"{ip}:{port_str}"
            self.lbl_status.setText(f"● CONNECTED  [{label}]")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.edit_ip.setEnabled(False)
            self.edit_port.setEnabled(False)
            self.check_sim.setEnabled(False)
            for b in self._move_btns:
                b.setEnabled(True)
            self.acs_connected.emit(ctrl)
            ctrl.start_polling(self._on_positions, self._on_states, self._on_lost)
            self._log(f"[ACS] 연결 성공 ({'시뮬레이터' if use_sim else label})")
        except Exception as e:
            err_str = str(e)
            if "No module named 'clr'" in err_str:
                err_str = "pythonnet 미설치 (pip install pythonnet)"
            self._log(f"[ACS] 연결 실패 — {err_str}")
            self._ctrl_ref[0] = None

    def _on_disconnect(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            ctrl.disconnect()
        self._ctrl_ref[0] = None
        self._set_disconnected_ui()
        self.acs_disconnected.emit()
        self._log("[ACS] 연결 해제")

    def _set_disconnected_ui(self):
        self.lbl_status.setText("● DISCONNECTED")
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.edit_ip.setEnabled(True)
        self.edit_port.setEnabled(True)
        self.check_sim.setEnabled(True)
        for b in self._move_btns:
            b.setEnabled(False)
        self.btn_sync_get_to_set.setEnabled(False)
        for row in self._axis_rows:
            row.lbl_pos.setText("---")
            row.update_state({'enabled': False, 'in_pos': False})

    # ── 폴링 콜백 ─────────────────────────────────────────────────────

    def _on_positions(self, positions: list):
        for i, row in enumerate(self._axis_rows):
            if i < len(positions) and positions[i] is not None:
                row.update_position(positions[i])
        
        # [Forward Kinematics] 실시간 DOF 계산 (모든 축 수신 시)
        if all(p is not None for p in positions[:6]):
            def run_fwd():
                try:
                    motor_arr = np.array(positions[:6], dtype=float)
                    res = self._calc.calculate_forward(motor_arr)
                    if res is not None:
                        # res: [Rx, Ry, Rz, Tx, Ty, Tz]
                        self._last_actual_dof = res
                        self._update_cur_dof_ui(res)
                except: pass
            threading.Thread(target=run_fwd, daemon=True).start()

    def _on_sync_get_to_set(self):
        """현재 실제 포즈(Get)를 입력 필드(Set)로 동기화."""
        if self._last_actual_dof is None:
            return
        
        # res: [Rx, Ry, Rz, Tx, Ty, Tz] (rad/mm)
        # vals: [Tx, Ty, Tz, Rx, Ry, Rz] (mm/mrad)
        res = self._last_actual_dof
        vals = [res[3], res[4], res[5], res[0]*1000.0, res[1]*1000.0, res[2]*1000.0]
        
        for spin, val in zip(self._dof_spins, vals):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)
        
        self._log("[KINEMATICS] Actual pose synced to input fields.")
        self._on_kin_calc()  # Sync 후 버튼 상태 업데이트

    def _update_cur_dof_ui(self, res: np.ndarray):
        # res: [Rx, Ry, Rz, Tx, Ty, Tz] (rad/mm)
        vals = [res[3], res[4], res[5], res[0]*1000.0, res[1]*1000.0, res[2]*1000.0]
        labels = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        for lbl, v in zip(labels, vals):
            if lbl in self._lbl_cur_dof:
                self._lbl_cur_dof[lbl].setText(f"{v:+.4f}")
        
        # 데이터가 있으면 Sync 버튼 활성화
        if not self.btn_sync_get_to_set.isEnabled():
            self.btn_sync_get_to_set.setEnabled(True)


    def _on_states(self, states: list):
        for i, row in enumerate(self._axis_rows):
            if i < len(states):
                # states[i] 는 {'enabled': bool, 'in_pos': bool} 형태의 dict
                row.update_state(states[i])

    def _on_lost(self):
        self._log("[ACS] 연결 끊김 감지")
        self._ctrl_ref[0] = None
        self._set_disconnected_ui()
        self.acs_disconnected.emit()

    # ── 전체 제어 ─────────────────────────────────────────────────────

    def _on_enable_all(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            threading.Thread(target=ctrl.enable_all, daemon=True).start()
            self._auto_disable_timer.start()  # 수동 Enable 시 5분 타이머 시작
            self._log("[ACS] ENABLE ALL (5분 후 자동 서보 OFF 예약)")

    def _on_disable_all(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            threading.Thread(target=ctrl.disable_all, daemon=True).start()
            self._auto_disable_timer.stop()
            self._log("[ACS] DISABLE ALL")

    def _on_stop_all(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            ctrl.stop_all()
            self._log("[ACS] STOP ALL 실행")

    # ── 키네마틱 계산 / 이동 ──────────────────────────────────────────

    def _on_kin_calc(self):
        trans  = [s.value() for s in self._dof_spins[:3]]
        rotate = [s.value() for s in self._dof_spins[3:]]
        cal, ball, ok, violations = self._calc.calculate(trans, rotate)

        if cal is None:
            self.kin_result.setPlainText(f"❌ 계산 실패\n{violations[0] if violations else ''}")
            self._last_cal_pos = None
            self.btn_kin_move.setEnabled(False)
            return

        lines = ["CalPos (mm):"]
        for name, pos, plus, minus in zip(
            ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"],
            cal,
            self._calc.plus_limits,
            self._calc.minus_limits,
        ):
            flag = "✅" if minus <= pos <= plus else "❌LIMIT"
            lines.append(f"  {name}: {pos:+10.4f}  {flag}")

        if ok:
            lines.append("→ 인터락 통과")
        else:
            lines.append("→ 인터락 위반 (클램핑 적용):")
            for v in violations:
                lines.append(f"   {v}")
                
        # [Actual DOF Calculation] 리밋에 걸렸을 때 실제 도달 위치 계산
        clamped, actual = self._calc.calculate_clamped(trans, rotate)
        if actual is not None:
            lines.append("\nAchievable DOF (Estimated):")
            labels = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
            for lbl, req, act in zip(labels, trans + rotate, actual):
                diff = abs(req - act)
                flag = "⚠" if diff > 0.001 else "✓"
                lines.append(f"  {lbl}: {act:+.4f} ({flag})")

        self.kin_result.setPlainText("\n".join(lines))

        self._last_cal_pos = cal if ok else None
        
        # MOVE 버튼 활성화 조건: 인터락 통과 AND (드라이런 OR 연결됨)
        is_dry = self.check_dry.isChecked()
        ctrl = self._ctrl_ref[0]
        self.btn_kin_move.setEnabled(ok and (is_dry or (ctrl is not None and ctrl.is_connected)))
 
        if ok:
            self._log("[KINEMATICS] Kinematic CALC: OK (인터락 통과)")
        else:
            v = violations[0] if violations else "?"
            self._log(f"[KINEMATICS] Kinematic CALC: LIMIT 위반 — {v}")

    def _on_kin_jog(self, index: int, sign: int):
        """[Rethink Move] 키네마틱 상대 이동: 현재 스핀박스 값에 Step을 가감하여 이동."""
        is_dry = self.check_dry.isChecked()
        if not is_dry:
            if not self._ctrl_ref[0] or not self._ctrl_ref[0].is_connected:
                self._log("ACS: 연결 후 조그 가능합니다.")
                return


        step = self.kin_jog_step.value()
        delta = sign * step
        spin = self._dof_spins[index]
        
        labels = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        self._log(f"[KINEMATICS] Kinematic Jog {labels[index]} ({'▲' if sign > 0 else '▼'}): {delta:+.4f}")
        
        # 1. 값 업데이트
        new_val = spin.value() + delta
        spin.setValue(new_val)
        
        # 2. 계산 수행
        self._on_kin_calc()
        
        # 3. 안전할 경우 즉시 이동
        if self.btn_kin_move.isEnabled():
            self._on_kin_move()
        else:
            self._log("[KINEMATICS] ⚠️ Kinematic Jog 중단: 계산 결과가 리밋을 벗어났습니다.")

    def _on_kin_move(self):
        if self._last_cal_pos is None:
            return
        if self._kin_worker and self._kin_worker.isRunning():
            self._log("⚠ KINEMATIC MOVE 진행 중 — 중복 명령 무시")
            return

        is_dry = self.check_dry.isChecked()
        ctrl   = self._ctrl_ref[0]

        tag = "  [DRY RUN — 실제 이동 없음]" if is_dry else ""
        
        # [Log Enhancement] 현재 위치(Get)와 목표 위치(Set)를 로그에 기록
        try:
            # Set values from spinboxes
            set_vals = [s.value() for s in self._dof_spins]
            set_s = ", ".join([f"{n}:{v:+.4f}" for n, v in zip(["Tx","Ty","Tz","Rx","Ry","Rz"], set_vals)])
            
            # Get values (Actual)
            if self._last_actual_dof is not None:
                r = self._last_actual_dof
                get_vals = [r[3], r[4], r[5], r[0]*1000.0, r[1]*1000.0, r[2]*1000.0]
                get_s = ", ".join([f"{n}:{v:+.4f}" for n, v in zip(["Tx","Ty","Tz","Rx","Ry","Rz"], get_vals)])
            else:
                get_s = "Unknown"
                
            self._log(f"[KINEMATICS] ▶ KINEMATIC MOVE 시작{tag}")
            self._log(f"   [SET] {set_s}")
            self._log(f"   [GET] {get_s}")
        except Exception:
            self._log(f"[KINEMATICS] ▶ KINEMATIC MOVE 시작{tag}")
        
        settle = self.spin_settle.value()
        self._auto_disable_timer.stop()
        self.btn_kin_move.setEnabled(False)

        self._kin_worker = _KinematicMoveWorker(
            ctrl, self._last_cal_pos, 
            self._calc.plus_limits, self._calc.minus_limits,
            settle, is_dry
        )
        self._kin_worker.log.connect(self._log)
        self._kin_worker.finished.connect(self._on_kin_move_done)
        self._kin_worker.error.connect(self._on_kin_move_error)
        self._kin_worker.start()

    def _on_kin_move_done(self):
        self.btn_kin_move.setEnabled(True)
        # 워커 시퀀스 내부에서 마지막에 disable_all()을 수행하므로 이미 OFF 상태임
        self._auto_disable_timer.stop() 
        self._log("[KINEMATICS] ✅ KINEMATIC MOVE 완료 (서보 OFF)")
 
    def _on_kin_move_error(self, msg: str):
        self.btn_kin_move.setEnabled(True)
        self._log(f"[KINEMATICS] ❌ KINEMATIC MOVE 오류: {msg}")

    def _on_auto_disable(self):
        ctrl = self._ctrl_ref[0]
        if ctrl and ctrl.is_connected:
            ctrl.disable_all()
            self._log("[ACS] ⏱ 자동 서보 OFF (5분 대기 타임아웃)")

    # ── 설정 ─────────────────────────────────────────────────────────

    def _on_dry_run(self, checked: bool):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            ctrl.dry_run = checked
        msg = "⚠ DRY RUN 활성 — 실제 모터 미작동" if checked else "▶ DRY RUN 해제 — 실제 명령 전송"
        self._log(f"[ACS] {msg}")
        self._on_kin_calc() # 버튼 상태 업데이트용
        self._save_settings()

    def _save_settings(self):
        self._settings.setValue(_SETTINGS_KEY_IP,   self.edit_ip.text().strip())
        self._settings.setValue(_SETTINGS_KEY_PORT,  self.edit_port.text().strip())
        self._settings.setValue(_SETTINGS_KEY_DRY,   self.check_dry.isChecked())
        self._settings.setValue("acs/settle_ms",    self.spin_settle.value())
        
        # Individual Steps 저장
        for i, spin in enumerate(self._dof_step_spins):
            self._settings.setValue(f"acs/kin_step_{i}", spin.value())

    def _load_settings(self):
        self.edit_ip.setText(self._settings.value(_SETTINGS_KEY_IP,   "10.0.0.100"))
        self.edit_port.setText(self._settings.value(_SETTINGS_KEY_PORT, str(DEFAULT_PORT)))
        self.check_dry.setChecked(self._settings.value(_SETTINGS_KEY_DRY, False, type=bool))
        self.spin_settle.setValue(self._settings.value("acs/settle_ms", 500, type=int))
        
        # Individual Steps 로드
        for i, spin in enumerate(self._dof_step_spins):
            val = self._settings.value(f"acs/kin_step_{i}", 0.1, type=float)
            spin.setValue(val)

    def set_controller(self, ctrl: AcsStageController):
        """외부에서 생성된 컨트롤러 주입 (DeepAlign 탭 등에서 공유 용도)."""
        if ctrl is None:
            return
        self._ctrl_ref[0] = ctrl
        if ctrl.is_connected:
            self.lbl_status.setText(f"● CONNECTED  [shared]")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.edit_ip.setEnabled(False)
            self.edit_port.setEnabled(False)
            self.check_sim.setEnabled(False)
            for b in self._move_btns:
                b.setEnabled(True)
            self.acs_connected.emit(ctrl)

    # ── 로그 ─────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_message.emit(msg)
