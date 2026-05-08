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
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QGroupBox, QGridLayout,
    QDoubleSpinBox, QCheckBox, QTextEdit,
)
from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtGui import QKeyEvent

from core.motor.acs_stage import AcsStageController, AXIS_LABELS, DEFAULT_PORT
from core.motor.kinematic_calc import KinematicCalc, is_available as kinematic_available
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_BG_DEEP, C_BG_DARK, C_TEXT, C_TEXT_DIM,
    Fonts, Sizes, BTN_SMALL, SPIN_STYLE, grp_style
)


# ── 스타일 토큰 ───────────────────────────────────────────────────────────────
_FC = "Courier New"
C_CYAN   = "#4ecdc4"
C_RED    = "#e94560"
C_AMBER  = "#ffe66d"
C_DIM    = "#8090b0"
C_BG     = "#080e1e"
C_BORDER = "#0f3460"

_GRP = """
    QGroupBox {{
        border: 1px solid {color}; border-radius: 6px;
        margin-top: 10px; font-family: 'Courier New';
        font-size: 14px; color: {color};
        letter-spacing: 2px; font-weight: bold;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
"""
_EDIT = f"""
    QLineEdit {{
        background: {C_BG}; border: 1px solid {C_BORDER};
        color: #c0d0ff; border-radius: 3px;
        font-family: '{_FC}'; font-size: 14px; padding: 2px 6px;
    }}
"""

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


def _spin_style() -> str:
    return f"""
        QDoubleSpinBox {{
            background: {C_BG}; border: 1px solid {C_BORDER};
            color: #c0d0ff; border-radius: 3px;
            font-family: '{_FC}'; font-size: 14px; padding: 1px 4px;
        }}
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
        lbl = QLabel(AXIS_LABELS[idx])
        lbl.setStyleSheet(f"color:{C_CYAN}; font-family:'{_FC}'; font-size:15px; font-weight:bold;")
        lbl.setFixedWidth(40)
        grid.addWidget(lbl, row, 0)

        # 위치 표시
        self.lbl_pos = QLabel("---")
        self.lbl_pos.setStyleSheet(
            f"color:#d8e8ff; font-family:'{_FC}'; font-size:15px; min-width:100px;"
        )
        self.lbl_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.lbl_pos, row, 1)

        # 모터 상태 인디케이터
        self.lbl_state = QLabel("●")
        self.lbl_state.setStyleSheet(f"color:#304060; font-size:13px;")
        self.lbl_state.setFixedWidth(26)
        grid.addWidget(self.lbl_state, row, 2)

        # Enable 버튼
        self.btn_en = QPushButton("EN")
        self.btn_en.setFixedWidth(44)
        self.btn_en.setStyleSheet(_btn(C_CYAN))
        self.btn_en.setEnabled(False)
        self.btn_en.clicked.connect(self._on_enable)
        move_btns.append(self.btn_en)
        grid.addWidget(self.btn_en, row, 3)

        # Disable 버튼
        self.btn_dis = QPushButton("DIS")
        self.btn_dis.setFixedWidth(46)
        self.btn_dis.setStyleSheet(_btn(C_RED))
        self.btn_dis.setEnabled(False)
        self.btn_dis.clicked.connect(self._on_disable)
        move_btns.append(self.btn_dis)
        grid.addWidget(self.btn_dis, row, 4)

        # 조그 스텝 입력
        self.spin_step = QDoubleSpinBox()
        self.spin_step.setRange(0.0001, 100.0)
        self.spin_step.setValue(0.1)
        self.spin_step.setDecimals(4)
        self.spin_step.setFixedWidth(90)
        self.spin_step.setStyleSheet(_spin_style())
        grid.addWidget(self.spin_step, row, 5)

        # − 버튼
        self.btn_minus = QPushButton("−")
        self.btn_minus.setFixedWidth(38)
        self.btn_minus.setStyleSheet(_btn(C_RED))
        self.btn_minus.setEnabled(False)
        self.btn_minus.clicked.connect(self._on_minus)
        move_btns.append(self.btn_minus)
        grid.addWidget(self.btn_minus, row, 6)

        # + 버튼
        self.btn_plus = QPushButton("+")
        self.btn_plus.setFixedWidth(38)
        self.btn_plus.setStyleSheet(_btn(C_CYAN))
        self.btn_plus.setEnabled(False)
        self.btn_plus.clicked.connect(self._on_plus)
        move_btns.append(self.btn_plus)
        grid.addWidget(self.btn_plus, row, 7)

    def update_position(self, pos: float):
        self.lbl_pos.setText(f"{pos:+.4f} mm")

    def update_state(self, enabled: bool):
        if enabled:
            self.lbl_state.setStyleSheet(f"color:{C_CYAN}; font-size:13px;")
        else:
            self.lbl_state.setStyleSheet("color:#304060; font-size:13px;")

    def _ctrl(self) -> AcsStageController | None:
        return self._ctrl_ref[0]

    def _on_enable(self):
        c = self._ctrl()
        if c:
            threading.Thread(target=c.enable_motor, args=(self.idx,), daemon=True).start()

    def _on_disable(self):
        c = self._ctrl()
        if c:
            threading.Thread(target=c.disable_motor, args=(self.idx,), daemon=True).start()

    def _on_minus(self):
        c = self._ctrl()
        if c:
            delta = -self.spin_step.value()
            self._log(f"ACS Jog- (Axis {self.idx}): {delta:+.4f} mm")
            def run():
                try:
                    c.move_by(self.idx, delta, wait=True)
                except Exception as e:
                    self._log(f"ACS Jog 실패: {e}")
            threading.Thread(target=run, daemon=True).start()

    def _on_plus(self):
        c = self._ctrl()
        if c:
            delta = self.spin_step.value()
            self._log(f"ACS Jog+ (Axis {self.idx}): {delta:+.4f} mm")
            def run():
                try:
                    c.move_by(self.idx, delta, wait=True)
                except Exception as e:
                    self._log(f"ACS Jog 실패: {e}")
            threading.Thread(target=run, daemon=True).start()


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
        self._lbl_cur_dof: dict[str, QLabel] = {} # 현재 도달 DOF 표시용


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

        # 5. Settings
        self.sec_set = CollapsibleSection("SETTINGS", accent="#9a6a4a", collapsed=True)
        self._build_settings_group(self.sec_set.content_layout())
        root.addWidget(self.sec_set)

        root.addStretch()

    def _build_conn_group(self, lay: QVBoxLayout):
        lay.setSpacing(4)

        # IP
        row_ip = QHBoxLayout()
        lbl = QLabel("IP")
        lbl.setStyleSheet(f"color:{C_DIM}; font-family:'{_FC}'; font-size:14px;")
        lbl.setFixedWidth(42)
        self.edit_ip = QLineEdit()
        self.edit_ip.setPlaceholderText("10.0.0.100")
        self.edit_ip.setStyleSheet(_EDIT)
        row_ip.addWidget(lbl)
        row_ip.addWidget(self.edit_ip)
        lay.addLayout(row_ip)


        # Port
        row_port = QHBoxLayout()
        lbl_p = QLabel("PORT")
        lbl_p.setStyleSheet(f"color:{C_DIM}; font-family:'{_FC}'; font-size:14px;")
        lbl_p.setFixedWidth(42)
        self.edit_port = QLineEdit(str(DEFAULT_PORT))
        self.edit_port.setFixedWidth(70)
        self.edit_port.setStyleSheet(_EDIT)
        self.check_sim = QCheckBox("SIM")
        self.check_sim.setStyleSheet(f"color:{C_AMBER}; font-family:'{_FC}'; font-size:14px;")
        row_port.addWidget(lbl_p)
        row_port.addWidget(self.edit_port)
        row_port.addStretch()
        row_port.addWidget(self.check_sim)
        lay.addLayout(row_port)

        # 버튼
        row_btn = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setStyleSheet(_btn(C_CYAN))
        self.btn_connect.clicked.connect(self._on_connect)

        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_disconnect.setStyleSheet(_btn(C_RED))
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.clicked.connect(self._on_disconnect)

        row_btn.addWidget(self.btn_connect)
        row_btn.addWidget(self.btn_disconnect)
        lay.addLayout(row_btn)

        # 상태
        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(
            f"color:{C_RED}; font-family:'{_FC}'; font-size:14px; font-weight:bold;"
        )
        lay.addWidget(self.lbl_status)


    def _build_axis_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        # 6축 그리드
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)

        # 헤더
        headers = ["Axis", "Position", "", "", "", "Step(mm)", "", ""]
        for col, txt in enumerate(headers):
            lbl = QLabel(txt)
            lbl.setStyleSheet(f"color:{C_DIM}; font-family:'{_FC}'; font-size:12px; padding-bottom:4px;")
            if col == 1:
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(lbl, 0, col)

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

        self.btn_en_all .setStyleSheet(_btn(C_CYAN))
        self.btn_dis_all.setStyleSheet(_btn(C_AMBER))
        self.btn_stop   .setStyleSheet(_btn(C_RED))

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


        # [Rethink Move] Kinematic Jog Step
        step_row = QHBoxLayout()
        step_row.setContentsMargins(4, 0, 4, 0)
        lbl_step = QLabel("Jog Step (mm/mrad):")
        lbl_step.setStyleSheet(f"color:{C_DIM}; font-family:'{_FC}'; font-size:12px;")
        self.kin_jog_step = QDoubleSpinBox()
        self.kin_jog_step.setRange(0.0001, 50.0)
        self.kin_jog_step.setValue(0.1)
        self.kin_jog_step.setDecimals(4)
        self.kin_jog_step.setFixedWidth(80)
        self.kin_jog_step.setStyleSheet(_spin_style())
        step_row.addWidget(lbl_step)
        step_row.addWidget(self.kin_jog_step)
        step_row.addStretch()
        lay.addLayout(step_row)

        # Trans / Rotate 입력 그리드 + Jog 버튼
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 4, 0, 4)

        labels = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        self._dof_spins: list[QDoubleSpinBox] = []
        for i, lbl_txt in enumerate(labels):
            # 2열 구성 (좌: T-axes, 우: R-axes)
            row, col_grp = divmod(i, 3)
            col_base = row * 4
            grid_row = col_grp
            
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(f"color:{C_CYAN}; font-family:'{_FC}'; font-size:13px; font-weight:bold;")
            lbl.setFixedWidth(24)
            grid.addWidget(lbl, grid_row, col_base)

            # Minus Button
            btn_m = QPushButton("−")
            btn_m.setFixedWidth(28)
            btn_m.setStyleSheet(_btn(C_RED))
            btn_m.setEnabled(not unavail)
            btn_m.clicked.connect(lambda _, idx=i: self._on_kin_jog(idx, -1))
            self._move_btns.append(btn_m)
            grid.addWidget(btn_m, grid_row, col_base + 1)

            # SpinBox
            spin = _DofSpinBox()
            spin.setRange(-500.0, 500.0)
            spin.setValue(0.0)
            spin.setDecimals(4)
            spin.setFixedWidth(85)
            spin.setStyleSheet(_spin_style())
            spin.setEnabled(not unavail)
            grid.addWidget(spin, grid_row, col_base + 2)
            self._dof_spins.append(spin)

            # Plus Button
            btn_p = QPushButton("+")
            btn_p.setFixedWidth(28)
            btn_p.setStyleSheet(_btn(C_CYAN))
            btn_p.setEnabled(not unavail)
            btn_p.clicked.connect(lambda _, idx=i: self._on_kin_jog(idx, 1))
            self._move_btns.append(btn_p)
            grid.addWidget(btn_p, grid_row, col_base + 3)

        lay.addLayout(grid)

        # CALC + MOVE 버튼 (하단)
        row_btn = QHBoxLayout()
        self.btn_kin_calc = QPushButton("CALC KINEMATICS")
        self.btn_kin_calc.setStyleSheet(_btn("#aa7acc"))
        self.btn_kin_calc.setEnabled(not unavail)
        self.btn_kin_calc.clicked.connect(self._on_kin_calc)

        self.btn_kin_move = QPushButton("EXECUTE MOVE")
        self.btn_kin_move.setStyleSheet(_btn(C_CYAN))
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
                background:{C_BG}; border:1px solid {C_BORDER};
                color:#c0d0ff; font-family:'{_FC}'; font-size:13px;
            }}
        """)
        if unavail:
            self.kin_result.setPlainText("⚠ AlignStageAlgorithm 로드 실패\n(AlignStageAlgorithm.py 확인)")
        lay.addWidget(self.kin_result)

        # 마지막 계산된 calPos 보관
        self._last_cal_pos = None

        # [Phase] 현재 실시간 도달 포지션 (Forward Kinematics)
        cur_grp = QGroupBox("LIVE  POSE (FORWARD)")
        cur_grp.setStyleSheet(_GRP.format(color=C_AMBER))
        cur_lay = QGridLayout(cur_grp)
        cur_lay.setContentsMargins(8, 14, 8, 8)
        cur_lay.setSpacing(4)
        
        for i, lbl_txt in enumerate(labels):
            row_idx, col_idx = divmod(i, 3)
            l = QLabel(f"{lbl_txt}:")
            l.setStyleSheet(f"color:{C_DIM}; font-family:'{_FC}'; font-size:12px;")
            val = QLabel("---")
            val.setStyleSheet(f"color:{C_AMBER}; font-family:'{_FC}'; font-size:13px; font-weight:bold;")
            cur_lay.addWidget(l, row_idx, col_idx * 2)
            cur_lay.addWidget(val, row_idx, col_idx * 2 + 1)
            self._lbl_cur_dof[lbl_txt] = val
            
        lay.addWidget(cur_grp)



    def _build_settings_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 8, 4, 8)
        self.check_dry = QCheckBox("DRY RUN (Simulate Moves)")
        self.check_dry.setStyleSheet(
            f"QCheckBox {{ color:{C_AMBER}; font-family:'{Fonts.UI}'; font-size:14px; font-weight:bold; }}"
        )
        self.check_dry.toggled.connect(self._on_dry_run)
        lay.addWidget(self.check_dry)



    # ── 연결 / 해제 ────────────────────────────────────────────────────

    def _on_connect(self):
        ip       = self.edit_ip.text().strip()
        port_str = self.edit_port.text().strip()
        use_sim  = self.check_sim.isChecked()

        if not use_sim and not ip:
            self._log("ACS: IP를 입력하세요")
            return

        self._save_settings()
        ctrl = AcsStageController()
        ctrl.dry_run = self.check_dry.isChecked()

        try:
            if use_sim:
                self._log("ACS: 시뮬레이터 연결 시도...")
                ctrl.connect_simulator()
            else:
                try:
                    port = int(port_str)
                except ValueError:
                    self._log("ACS: Port는 정수여야 합니다")
                    return
                self._log(f"ACS: 연결 시도 → {ip}:{port}")
                ctrl.connect(ip, port)

            self._ctrl_ref[0] = ctrl
            label = "SIMULATOR" if use_sim else f"{ip}:{port_str}"
            self.lbl_status.setText(f"● CONNECTED  [{label}]")
            self.lbl_status.setStyleSheet(
                f"color:{C_CYAN}; font-family:'{_FC}'; font-size:14px; font-weight:bold;"
            )
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.edit_ip.setEnabled(False)
            self.edit_port.setEnabled(False)
            self.check_sim.setEnabled(False)
            for b in self._move_btns:
                b.setEnabled(True)
            self.acs_connected.emit(ctrl)
            ctrl.start_polling(self._on_positions, self._on_states, self._on_lost)
            self._log(f"ACS: 연결 성공 ({'시뮬레이터' if use_sim else label})")
        except Exception as e:
            err_str = str(e)
            if "No module named 'clr'" in err_str:
                err_str = "pythonnet 미설치 (pip install pythonnet)"
            self._log(f"ACS: 연결 실패 — {err_str}")
            self._ctrl_ref[0] = None

    def _on_disconnect(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            ctrl.disconnect()
        self._ctrl_ref[0] = None
        self._set_disconnected_ui()
        self.acs_disconnected.emit()
        self._log("ACS: 연결 해제")

    def _set_disconnected_ui(self):
        self.lbl_status.setText("● DISCONNECTED")
        self.lbl_status.setStyleSheet(
            f"color:{C_RED}; font-family:'{_FC}'; font-size:14px; font-weight:bold;"
        )
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.edit_ip.setEnabled(True)
        self.edit_port.setEnabled(True)
        self.check_sim.setEnabled(True)
        for b in self._move_btns:
            b.setEnabled(False)
        for row in self._axis_rows:
            row.lbl_pos.setText("---")
            row.update_state(False)

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
                        self._update_cur_dof_ui(res)
                except: pass
            threading.Thread(target=run_fwd, daemon=True).start()

    def _update_cur_dof_ui(self, res: np.ndarray):
        # UI 업데이트는 메인 스레드에서 (임시로 threading 내에서 직접 호출하나 원래는 Signal 사용 권장)
        # 하지만 QLabel.setText는 단순해서 대부분의 경우 안전함.
        # res: [Rx, Ry, Rz, Tx, Ty, Tz] (rad/mm)
        vals = [res[3], res[4], res[5], res[0]*1000.0, res[1]*1000.0, res[2]*1000.0]
        labels = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        for lbl, v in zip(labels, vals):
            if lbl in self._lbl_cur_dof:
                self._lbl_cur_dof[lbl].setText(f"{v:+.4f}")


    def _on_states(self, states: list):
        for i, row in enumerate(self._axis_rows):
            if i < len(states):
                row.update_state(states[i])

    def _on_lost(self):
        self._log("ACS: 연결 끊김 감지")
        self._ctrl_ref[0] = None
        self._set_disconnected_ui()
        self.acs_disconnected.emit()

    # ── 전체 제어 ─────────────────────────────────────────────────────

    def _on_enable_all(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            threading.Thread(target=ctrl.enable_all, daemon=True).start()

    def _on_disable_all(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            threading.Thread(target=ctrl.disable_all, daemon=True).start()

    def _on_stop_all(self):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            ctrl.stop_all()
            self._log("ACS: STOP ALL 실행")

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
            self._log("ACS Kinematic CALC: OK (인터락 통과)")
        else:
            v = violations[0] if violations else "?"
            self._log(f"ACS Kinematic CALC: LIMIT 위반 — {v}")

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
        self._log(f"ACS Kinematic Jog {labels[index]} ({'▲' if sign > 0 else '▼'}): {delta:+.4f}")
        
        # 1. 값 업데이트
        new_val = spin.value() + delta
        spin.setValue(new_val)
        
        # 2. 계산 수행
        self._on_kin_calc()
        
        # 3. 안전할 경우 즉시 이동
        if self.btn_kin_move.isEnabled():
            self._on_kin_move()
        else:
            self._log("⚠️ Kinematic Jog 중단: 계산 결과가 리밋을 벗어났습니다.")

    def _on_kin_move(self):
        if self._last_cal_pos is None:
            return
        
        is_dry = self.check_dry.isChecked()
        ctrl = self._ctrl_ref[0]
        
        if not is_dry:
            if ctrl is None or not ctrl.is_connected:
                self._log("ACS: 연결 후 이동 가능")
                return


        cal  = self._last_cal_pos.copy()
        tag  = "  [DRY RUN — 실제 이동 없음]" if is_dry else ""
        self._log(f"ACS Kinematic MOVE 시작{tag}")
        self.btn_kin_move.setEnabled(False)
        try:
            if not is_dry and ctrl:
                for i, target in enumerate(cal):
                    ctrl.move_to(i, float(target), wait=False)
                self._log("ACS Kinematic MOVE 명령 전송 완료")
            else:
                self._log("ACS Kinematic MOVE 시뮬레이션 완료")
        except Exception as e:
            self._log(f"ACS Kinematic MOVE 오류: {e}")
        finally:
            self.btn_kin_move.setEnabled(True)

    # ── 설정 ─────────────────────────────────────────────────────────

    def _on_dry_run(self, checked: bool):
        ctrl = self._ctrl_ref[0]
        if ctrl:
            ctrl.dry_run = checked
        msg = "⚠ DRY RUN 활성 — 실제 모터 미작동" if checked else "▶ DRY RUN 해제 — 실제 명령 전송"
        self._log(f"ACS: {msg}")
        self._on_kin_calc() # 버튼 상태 업데이트용
        self._save_settings()

    def _save_settings(self):
        self._settings.setValue(_SETTINGS_KEY_IP,   self.edit_ip.text().strip())
        self._settings.setValue(_SETTINGS_KEY_PORT,  self.edit_port.text().strip())
        self._settings.setValue(_SETTINGS_KEY_DRY,   self.check_dry.isChecked())
        self._settings.setValue("acs/kin_jog_step", self.kin_jog_step.value())

    def _load_settings(self):
        self.edit_ip.setText(self._settings.value(_SETTINGS_KEY_IP,   "10.0.0.100"))
        self.edit_port.setText(self._settings.value(_SETTINGS_KEY_PORT, str(DEFAULT_PORT)))
        self.check_dry.setChecked(self._settings.value(_SETTINGS_KEY_DRY, False, type=bool))
        self.kin_jog_step.setValue(self._settings.value("acs/kin_jog_step", 0.1, type=float))

    # ── 로그 ─────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_message.emit(msg)
