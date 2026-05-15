"""
ui/widgets/acs_card.py
ACS SPiiPlus 6축 키네마틱 스테이지 제어 카드 — 전용 위젯 버전.
이미지 사양에 맞춘 FULL 기능 (Connection, Status, Global, Kinematic Move).
"""

from __future__ import annotations

import threading
import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QGridLayout,
    QDoubleSpinBox, QSpinBox, QCheckBox, QTextEdit, QFrame
)
from PyQt6.QtCore import Qt, QSettings, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent

from core.motor.acs_stage import AcsStageController, AXIS_LABELS, DEFAULT_PORT
from core.motor.kinematic_calc import KinematicCalc, is_available as kinematic_available
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_BG_DEEP, C_BG_DARK, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD,
    Fonts, Sizes, BTN_SMALL, SPIN_STYLE, EDIT_STYLE, CHECKBOX_STYLE, lbl
)

# ── 로컬 스타일 헬퍼 ──────────────────────────────────────────────
_FC       = Fonts.MONO
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
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            key = event.key()
            if key == Qt.Key.Key_C: self.lineEdit().copy(); return
            if key == Qt.Key.Key_V: self.lineEdit().paste(); return
            if key == Qt.Key.Key_A: self.lineEdit().selectAll(); return
        super().keyPressEvent(event)

class _AxisRow:
    def __init__(self, idx: int, grid: QGridLayout, ctrl_ref: list):
        self.idx = idx
        self._ctrl_ref = ctrl_ref

        # 1. Axis Name
        self.lbl_name = QLabel(f"{AXIS_LABELS[idx]}")
        self.lbl_name.setStyleSheet(lbl(C_ACCENT, bold=True, mono=True))
        grid.addWidget(self.lbl_name, idx + 1, 0)

        # 2. Position
        self.lbl_pos = QLabel("---")
        self.lbl_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_pos.setStyleSheet(lbl(C_TEXT, mono=True))
        grid.addWidget(self.lbl_pos, idx + 1, 1)

        unit = QLabel("mm")
        unit.setStyleSheet(lbl(C_TEXT_DEAD, size="10px"))
        grid.addWidget(unit, idx + 1, 2)

        # 3. Servo Status
        self.lbl_servo = QLabel("OFF")
        self.lbl_servo.setFixedWidth(60)
        self.lbl_servo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_servo.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; border: 1px solid #334155; border-radius: 4px;")
        grid.addWidget(self.lbl_servo, idx + 1, 3)

        # 4. Done (Not Moving) LED
        self.lbl_done = QLabel("●")
        self.lbl_done.setFixedWidth(40)
        self.lbl_done.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_done.setStyleSheet("color:#304060; font-size:16px;")
        grid.addWidget(self.lbl_done, idx + 1, 4)

        # 5. In-Position LED
        self.lbl_inpos = QLabel("●")
        self.lbl_inpos.setFixedWidth(40)
        self.lbl_inpos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_inpos.setStyleSheet("color:#304060; font-size:16px;")
        grid.addWidget(self.lbl_inpos, idx + 1, 5)

    def update_position(self, pos: float):
        self.lbl_pos.setText(f"{pos:+.4f} mm")

    def update_state(self, state: dict | bool):
        if state is None: # Disconnected case
            self.lbl_servo.setText("OFF")
            self.lbl_servo.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; border: 1px solid #334155; border-radius: 4px;")
            self.lbl_done.setStyleSheet("color:#304060; font-size:16px;")
            self.lbl_inpos.setStyleSheet("color:#304060; font-size:16px;")
            return

        if isinstance(state, dict):
            enabled = state.get("enabled", False)
            moving  = state.get("moving",  False)
            in_pos  = state.get("in_pos",  False)
        else:
            enabled = bool(state); moving = False; in_pos = False
            
        if enabled:
            self.lbl_servo.setText("ENABLED")
            self.lbl_servo.setStyleSheet("color: #14b8a6; font-size: 11px; font-weight: bold; border: 1px solid #14b8a6; border-radius: 4px; background: rgba(20,184,166,0.1);")
        else:
            self.lbl_servo.setText("OFF")
            self.lbl_servo.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; border: 1px solid #334155; border-radius: 4px;")

        done_color = "#10b981" if (not moving) else "#f59e0b"
        self.lbl_done.setStyleSheet(f"color:{done_color}; font-size:16px;")
        inpos_color = "#10b981" if in_pos else "#304060"
        self.lbl_inpos.setStyleSheet(f"color:{inpos_color}; font-size:16px;")

class _KinematicMoveWorker(QThread):
    log      = pyqtSignal(str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)
    
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
        self._stop_requested = False

    def stop(self): self._stop_requested = True

    def run(self):
        try:
            if self._dry:
                self.log.emit("[DRY RUN] 시뮬레이션 이동 중...")
                self.msleep(1000)
                self.finished.emit(); return

            self.log.emit("[ACS] Servo ON...")
            self._ctrl.enable_all()
            if not self._ctrl.wait_for_enabled_all(timeout_ms=1000):
                raise RuntimeError("Servo ON 확인 실패")

            self.log.emit("[ACS] Move 시작...")
            for i, target in enumerate(self._targets):
                if self._stop_requested: return
                self._ctrl.move_to(i, float(target), wait=False)

            self.log.emit("[ACS] In-Position 대기...")
            self._ctrl.wait_in_position_all(timeout_ms=30000)
            self.msleep(self._settle_ms)

            self.log.emit("[ACS] Servo OFF...")
            self._ctrl.disable_all()
            self.log.emit("[ACS] 이동 완료")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

class AcsCard(QFrame):
    """
    ACS 6-Axis Stage 카드 (전체 기능 포함).
    """
    log_message = pyqtSignal(str)
    acs_connected = pyqtSignal(object)
    acs_disconnected = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("motionCard")
        self.setStyleSheet("""
            QFrame#motionCard {
                background: #0f1729;
                border: 1px solid #11345f;
                border-radius: 6px;
            }
        """)
        
        self._ctrl_ref: list[AcsStageController | None] = [None]
        self._move_btns: list[QPushButton] = []
        self._axis_rows: list[_AxisRow] = []
        self._motion_widgets: list[QWidget] = []
        self._session_hub = None
        self._settings = QSettings("SpeAnalyze", "MainWindow")
        self._calc = KinematicCalc()
        self._kin_worker: _KinematicMoveWorker | None = None
        self._last_actual_dof = None
        self._is_fwd_calculating = False

        self._build_ui()
        self._load_settings()
        self._set_disconnected_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        title = QLabel("▾  ACS KINEMATIC STAGE")
        title.setStyleSheet(f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: 18px; font-weight: bold; letter-spacing: 2px;")
        lay.addWidget(title)

        # 1. Connection
        self.sec_conn = CollapsibleSection("CONNECTION", accent=C_ACCENT)
        conn_l = self.sec_conn.content_layout()
        
        row_ip = QHBoxLayout()
        self.edit_ip = QLineEdit(); self.edit_ip.setPlaceholderText("10.0.0.100"); self.edit_ip.setStyleSheet(EDIT_STYLE)
        self.edit_port = QLineEdit("700"); self.edit_port.setFixedWidth(60); self.edit_port.setStyleSheet(EDIT_STYLE)
        self.check_sim = QCheckBox("SIM"); self.check_sim.setStyleSheet(CHECKBOX_STYLE)
        row_ip.addWidget(QLabel("IP")); row_ip.addWidget(self.edit_ip); row_ip.addWidget(QLabel("PORT")); row_ip.addWidget(self.edit_port); row_ip.addWidget(self.check_sim)
        conn_l.addLayout(row_ip)

        row_btn = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT"); self.btn_connect.setStyleSheet(BTN_SMALL)
        self.btn_disconnect = QPushButton("DISCONNECT"); self.btn_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        row_btn.addWidget(self.btn_connect); row_btn.addWidget(self.btn_disconnect)
        conn_l.addLayout(row_btn)

        self.lbl_status = QLabel("● DISCONNECTED"); self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter); self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        conn_l.addWidget(self.lbl_status)
        lay.addWidget(self.sec_conn)

        # 2. Status
        self.sec_axis = CollapsibleSection("6-AXIS POSITIONS", accent=C_DANGER)
        axis_l = self.sec_axis.content_layout()
        grid = QGridLayout(); grid.setSpacing(4)
        for i, h in enumerate(["Axis", "Position", "", "Servo", "Done", "InPos"]):
            lbl_h = QLabel(h); lbl_h.setStyleSheet(lbl(C_TEXT_DIM, size="11px", mono=True)); grid.addWidget(lbl_h, 0, i)
        for i in range(6):
            row = _AxisRow(i, grid, self._ctrl_ref)
            self._axis_rows.append(row)
        axis_l.addLayout(grid)
        lay.addWidget(self.sec_axis)

        # 3. Global Control
        self.sec_global = CollapsibleSection("GLOBAL CONTROL", accent=C_ACCENT)
        glob_l = self.sec_global.content_layout()
        row_g = QHBoxLayout()
        self.btn_en_all = QPushButton("ENABLE ALL"); self.btn_dis_all = QPushButton("DISABLE ALL"); self.btn_stop = QPushButton("STOP ALL")
        for b in (self.btn_en_all, self.btn_dis_all, self.btn_stop):
            b.setStyleSheet(_btn(C_ACCENT if b==self.btn_en_all else (C_WARN if b==self.btn_dis_all else C_DANGER)))
            row_g.addWidget(b)
        glob_l.addLayout(row_g)
        lay.addWidget(self.sec_global)

        # 4. Kinematic Move
        self.sec_kin = CollapsibleSection("6DOF KINEMATIC MOVE", accent="#aa7acc")
        kin_l = self.sec_kin.content_layout()
        grid_k = QGridLayout(); grid_k.setSpacing(4)
        headers = ["DOF", "ACTUAL (GET)", "TARGET (SET)", "STEP", "", ""]
        for col, txt in enumerate(headers):
            h_lbl = QLabel(txt); h_lbl.setStyleSheet(lbl(C_TEXT_DIM, size="10px", mono=True)); grid_k.addWidget(h_lbl, 0, col)

        dofs = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        self._dof_spins = []; self._dof_step_spins = []; self._lbl_cur_dof = {}
        for i, dof in enumerate(dofs):
            row_idx = i + 1
            grid_k.addWidget(QLabel(dof), row_idx, 0)
            v_get = QLabel("---"); v_get.setStyleSheet(lbl(C_WARN, bold=True, mono=True)); grid_k.addWidget(v_get, row_idx, 1); self._lbl_cur_dof[dof] = v_get
            sp = _DofSpinBox(); sp.setRange(-500, 500); sp.setDecimals(4); sp.setStyleSheet(_spin_style()); grid_k.addWidget(sp, row_idx, 2); self._dof_spins.append(sp)
            step = QDoubleSpinBox(); step.setRange(0.0001, 100); step.setValue(0.1); step.setDecimals(4); step.setStyleSheet(_spin_style("#1e293b")); grid_k.addWidget(step, row_idx, 3); self._dof_step_spins.append(step)
            bm = QPushButton("−"); bm.setStyleSheet(_btn(C_DANGER)); grid_k.addWidget(bm, row_idx, 4); bm.clicked.connect(lambda _, x=i: self._on_kin_jog(x, -1)); self._move_btns.append(bm)
            bp = QPushButton("+"); bp.setStyleSheet(_btn(C_ACCENT)); grid_k.addWidget(bp, row_idx, 5); bp.clicked.connect(lambda _, x=i: self._on_kin_jog(x, 1)); self._move_btns.append(bp)
        kin_l.addLayout(grid_k)

        self.btn_sync = QPushButton("SYNC ACTUAL (GET) → TARGET (SET)"); self.btn_sync.setStyleSheet(_btn(C_WARN).replace("transparent", "#1a1510")); kin_l.addWidget(self.btn_sync)
        
        row_exec = QHBoxLayout()
        self.btn_kin_calc = QPushButton("CALC KINEMATICS"); self.btn_kin_calc.setStyleSheet(_btn("#aa7acc"))
        self.btn_kin_move = QPushButton("EXECUTE MOVE"); self.btn_kin_move.setStyleSheet(_btn(C_ACCENT))
        row_exec.addWidget(self.btn_kin_calc); row_exec.addWidget(self.btn_kin_move)
        kin_l.addLayout(row_exec)

        self.kin_result = QTextEdit(); self.kin_result.setReadOnly(True); self.kin_result.setFixedHeight(80); self.kin_result.setStyleSheet(f"background:{_C_BG}; color:#c0d0ff; font-family:'{_FC}';"); kin_l.addWidget(self.kin_result)
        
        row_dry = QHBoxLayout()
        self.check_dry = QCheckBox("DRY RUN"); self.check_dry.setStyleSheet(f"color:{C_WARN}; font-weight:bold;")
        self.spin_settle = QSpinBox(); self.spin_settle.setRange(0, 5000); self.spin_settle.setValue(500); self.spin_settle.setStyleSheet(_spin_style())
        row_dry.addWidget(self.check_dry); row_dry.addStretch(); row_dry.addWidget(QLabel("Settle(ms):")); row_dry.addWidget(self.spin_settle)
        kin_l.addLayout(row_dry)
        lay.addWidget(self.sec_kin)
        lay.addStretch()

        # Connect
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_en_all.clicked.connect(self._on_enable_all)
        self.btn_dis_all.clicked.connect(self._on_disable_all)
        self.btn_stop.clicked.connect(self._on_stop_all)
        self.btn_kin_calc.clicked.connect(self._on_kin_calc)
        self.btn_kin_move.clicked.connect(self._on_kin_move)
        self.btn_sync.clicked.connect(self._on_sync_get_to_set)

    def _set_disconnected_ui(self):
        self.lbl_status.setText("● DISCONNECTED"); self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        self.btn_connect.setEnabled(True); self.btn_disconnect.setEnabled(False)
        self.edit_ip.setEnabled(True); self.edit_port.setEnabled(True); self.check_sim.setEnabled(True)
        for b in self._move_btns: b.setEnabled(False)
        self.btn_en_all.setEnabled(False); self.btn_dis_all.setEnabled(False); self.btn_stop.setEnabled(False)
        self.btn_kin_calc.setEnabled(kinematic_available()); self.btn_kin_move.setEnabled(False); self.btn_sync.setEnabled(False)
        for row in self._axis_rows:
            row.lbl_pos.setText("---")
            row.update_state(None)

    def _on_connect(self):
        ip = self.edit_ip.text().strip()
        port_txt = self.edit_port.text().strip()
        port = int(port_txt) if port_txt else 700
        sim = self.check_sim.isChecked()
        
        self._save_settings()
        
        if self._session_hub:
            try:
                self._session_hub.acs_connect(ip, port, sim)
            except Exception as e:
                self.log_message.emit(f"ACS Connect (Hub) Failed: {e}")
        else:
            ctrl = AcsStageController()
            try:
                if sim: ctrl.connect_simulator()
                else: ctrl.connect(ip, port)
                self.set_controller(ctrl)
                self.log_message.emit("ACS Connected (Standalone)")
            except Exception as e:
                self.log_message.emit(f"ACS Connect Failed: {e}")

    def update_status(self, connected, pos, states=None):
        if not connected:
            self._set_disconnected_ui()
            return

        if pos: self._on_positions(pos)
        if states: self._on_states(states)
        
        if self.btn_connect.isEnabled():
            self.lbl_status.setText(f"● CONNECTED")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.btn_connect.setEnabled(False); self.btn_disconnect.setEnabled(True)
            for b in self._move_btns: b.setEnabled(True)
            self.btn_en_all.setEnabled(True); self.btn_dis_all.setEnabled(True); self.btn_stop.setEnabled(True)
            self.btn_sync.setEnabled(True)

    def bind_session_hub(self, hub):
        self._session_hub = hub
        if hub:
            hub.event_published.connect(self._on_session_event)
            if hub.is_acs_connected():
                ctrl = getattr(hub, "acs_controller", None)
                if ctrl: self.set_controller(ctrl)

    def set_controller(self, ctrl):
        self._ctrl_ref[0] = ctrl
        if ctrl and ctrl.is_connected:
            self.update_status(True, ctrl.get_positions(), ctrl.get_axis_states())
            
    def _on_session_event(self, event):
        from core.session.session_events import SessionEventType
        if event.event_type == SessionEventType.ACS_CONNECTED:
            ctrl = getattr(self._session_hub, "acs_controller", None)
            if ctrl: self.set_controller(ctrl)
        elif event.event_type == SessionEventType.ACS_DISCONNECTED:
            self._on_disconnect()

    def _on_disconnect(self):
        if self._session_hub: self._session_hub.acs_disconnect()
        elif self._ctrl_ref[0]: self._ctrl_ref[0].disconnect()
        self._ctrl_ref[0] = None; self._set_disconnected_ui(); self.acs_disconnected.emit()

    def _on_lost(self): self._on_disconnect(); self.log_message.emit("ACS Connection Lost")

    def _on_positions(self, pos):
        if not pos: return
        for i, row in enumerate(self._axis_rows):
            if i < len(pos) and pos[i] is not None: row.update_position(pos[i])
        
        if all(p is not None for p in pos[:6]) and not self._is_fwd_calculating:
            self._is_fwd_calculating = True
            def run():
                try:
                    res = self._calc.calculate_forward(np.array(pos[:6]))
                    if res is not None:
                        self._last_actual_dof = res
                        dofs = ["Rx", "Ry", "Rz", "Tx", "Ty", "Tz"]
                        for i, dof in enumerate(dofs):
                            val = res[i] * 1000 if i < 3 else res[i]
                            self._lbl_cur_dof[dof].setText(f"{val:+.4f}")
                finally: self._is_fwd_calculating = False
            threading.Thread(target=run, daemon=True).start()

    def _on_states(self, states):
        if not states: return
        for i, row in enumerate(self._axis_rows):
            if i < len(states): row.update_state(states[i])

    def _on_enable_all(self):
        if self._session_hub: self._session_hub.acs_enable_all()
        elif self._ctrl_ref[0]: self._ctrl_ref[0].enable_all()
    def _on_disable_all(self):
        if self._session_hub: self._session_hub.acs_disable_all()
        elif self._ctrl_ref[0]: self._ctrl_ref[0].disable_all()
    def _on_stop_all(self):
        if self._session_hub: self._session_hub.acs_stop_all()
        elif self._ctrl_ref[0]: self._ctrl_ref[0].stop_all()

    def _on_kin_jog(self, idx, direction):
        step = self._dof_step_spins[idx].value()
        self._dof_spins[idx].setValue(self._dof_spins[idx].value() + step * direction)
        self._on_kin_calc()

    def _on_kin_calc(self):
        t_vals = [self._dof_spins[i].value() for i in range(3)]
        r_vals = [self._dof_spins[i+3].value() for i in range(3)]
        pos, ball, ok, viols = self._calc.calculate(t_vals, r_vals)
        if pos is not None:
            self._last_cal_pos = pos
            res_txt = f"RESULT: {'OK' if ok else 'LIMIT!'}\n" + "\n".join([f"M{i+1}: {p:+.4f}" for i, p in enumerate(pos)])
            if viols: res_txt += "\n\n" + "\n".join(viols)
            self.kin_result.setPlainText(res_txt)
            self.btn_kin_move.setEnabled(ok)
        else: self.kin_result.setPlainText("CALCULATION FAILED")

    def _on_kin_move(self):
        if self._last_cal_pos is None or not self._ctrl_ref[0]: return
        self._kin_worker = _KinematicMoveWorker(self._ctrl_ref[0], self._last_cal_pos, self._calc.plus_limits, self._calc.minus_limits, self.spin_settle.value(), self.check_dry.isChecked())
        self._kin_worker.log.connect(self.log_message.emit); self._kin_worker.finished.connect(lambda: self.log_message.emit("ACS: Move Finished"))
        self._kin_worker.start()

    def _on_sync_get_to_set(self):
        if self._last_actual_dof is None: return
        res = self._last_actual_dof # [Rx, Ry, Rz, Tx, Ty, Tz]
        for i in range(3): self._dof_spins[i].setValue(res[i+3])
        for i in range(3): self._dof_spins[i+3].setValue(res[i] * 1000)

    def _save_settings(self):
        self._settings.setValue("acs/ip", self.edit_ip.text())
        self._settings.setValue("acs/port", self.edit_port.text())

    def _load_settings(self):
        self.edit_ip.setText(self._settings.value("acs/ip", "10.0.0.100"))
        self.edit_port.setText(self._settings.value("acs/port", "700"))
