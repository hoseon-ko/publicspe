"""
ui/live/kimm_z_panel.py
KIMM Fine Stage Z축 연결 + 위치 조회 패널 — SpeAnalyze 다크 테마.

기능:
  - IP / Port 입력 후 Connect / Disconnect
  - 100 ms 폴링으로 Z 위치 표시
  - Servo 상태 표시
  - 수동 제어 (Jog / Absolute Move)
"""
from __future__ import annotations
import threading
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QGroupBox,
    QDoubleSpinBox, QCheckBox, QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal

from core.motor.kimm_z import KIMMZController
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_BG_DEEP, C_BG_DARK, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD,
    Fonts, Sizes, BTN_SMALL, SPIN_STYLE, EDIT_STYLE, grp_style, lbl
)


# ── 설정 키 ─────────────────────────────────────────────────────────

_SETTINGS_KEY_IP     = "kimm/ip"
_SETTINGS_KEY_PORT   = "kimm/port"
_SETTINGS_KEY_LIMIT  = "kimm/safety_limit"
_SETTINGS_KEY_VEL    = "kimm/velocity"
_SETTINGS_KEY_DRY    = "kimm/dry_run"


class KIMMZPanel(QWidget):
    """KIMM Z축 위치 표시 + 연결 관리 패널."""

    log_message = pyqtSignal(str)   # 상위 시스템 로그로 전달
    kimm_connected = pyqtSignal(object)
    kimm_disconnected = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctrl: KIMMZController | None = None
        self._move_btns: list[QPushButton] = []   # 이동 버튼들 (연결 시 활성화)
        self._settings = QSettings("SpeAnalyze", "MainWindow")

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll)

        self._build_ui()
        self._load_settings()

    def stop_polling(self):
        """종료 시 폴링 타이머 정지."""
        if hasattr(self, "_poll_timer") and self._poll_timer.isActive():
            self._poll_timer.stop()
        if self._ctrl:
            self._ctrl.disconnect()

    # ── UI 구성 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # 1. Connection
        self.sec_conn = CollapsibleSection("KIMM FINE STAGE (Z)", accent=C_ACCENT)
        self._build_conn_group(self.sec_conn.content_layout())
        root.addWidget(self.sec_conn)

        # 2. Status & Position
        self.sec_stat = CollapsibleSection("STATUS & POSITION", accent=C_DANGER)
        self._build_status_group(self.sec_stat.content_layout())
        root.addWidget(self.sec_stat)

        # 3. Manual Control
        self.sec_ctrl = CollapsibleSection("MANUAL CONTROL", accent=C_ACCENT)
        self._build_control_group(self.sec_ctrl.content_layout())
        root.addWidget(self.sec_ctrl)

        # 4. Settings (기본 접힘)
        self.sec_set = CollapsibleSection("SETTINGS", accent=C_WARN, collapsed=True)
        self._build_settings_group(self.sec_set.content_layout())
        root.addWidget(self.sec_set)

        # 섹션 변경 시 자동 저장 연결
        self._sections = [self.sec_conn, self.sec_stat, self.sec_ctrl, self.sec_set]
        for sec in self._sections:
            sec.toggled.connect(self._save_settings)

        root.addStretch()

    def _build_conn_group(self, lay: QVBoxLayout):
        lay.setSpacing(6)
        lay.setContentsMargins(4, 4, 4, 4)

        # IP 입력
        row_ip = QHBoxLayout()
        lbl_ip = QLabel("IP")
        lbl_ip.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_ip.setFixedWidth(50)
        self.edit_ip = QLineEdit()
        self.edit_ip.setPlaceholderText("192.168.1.100")
        self.edit_ip.setStyleSheet(EDIT_STYLE)
        row_ip.addWidget(lbl_ip)
        row_ip.addWidget(self.edit_ip)
        lay.addLayout(row_ip)

        # Port 입력
        row_port = QHBoxLayout()
        lbl_port = QLabel("PORT")
        lbl_port.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_port.setFixedWidth(50)
        self.edit_port = QLineEdit()
        self.edit_port.setPlaceholderText("5000")
        self.edit_port.setFixedWidth(80)
        self.edit_port.setStyleSheet(EDIT_STYLE)
        row_port.addWidget(lbl_port)
        row_port.addWidget(self.edit_port)
        row_port.addStretch()
        lay.addLayout(row_port)

        # 버튼
        row_btn = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setStyleSheet(BTN_SMALL)
        self.btn_connect.clicked.connect(self._on_connect)

        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_disconnect.clicked.connect(self._on_disconnect)

        row_btn.addWidget(self.btn_connect)
        row_btn.addWidget(self.btn_disconnect)
        lay.addLayout(row_btn)

        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        lay.addWidget(self.lbl_status)

    def _build_status_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 4, 4, 4)
        # Z 위치 (큰 숫자)
        self.lbl_z = QLabel("---  um")
        self.lbl_z.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_z.setStyleSheet(
            f"color:#d8e8ff; font-family:'{Fonts.MONO}'; font-size:26px; font-weight:bold;"
        )
        lay.addWidget(self.lbl_z)

        # Servo 상태
        row_servo = QHBoxLayout()
        lbl_s = QLabel("SERVO")
        lbl_s.setStyleSheet(lbl(C_TEXT_DIM))
        self.lbl_servo = QLabel("OFF")
        self.lbl_servo.setStyleSheet(lbl(C_TEXT_DEAD, bold=True, mono=True))
        row_servo.addWidget(lbl_s)
        row_servo.addStretch()
        row_servo.addWidget(self.lbl_servo)
        lay.addLayout(row_servo)

    def _build_control_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 4, 4, 4)
        # 조그 버튼 (3열 2행 그리드)
        jog_grid = QGridLayout()
        
        def add_jog(text: str, val: float, r: int, c: int, color: str):
            btn = QPushButton(text)
            btn.setStyleSheet(BTN_SMALL.replace(C_ACCENT, color))
            btn.setEnabled(False)
            btn.clicked.connect(lambda _, v=val: self._on_jog(v))
            jog_grid.addWidget(btn, r, c)
            self._move_btns.append(btn)

        add_jog("+10",  10.0, 0, 0, C_ACCENT)
        add_jog("+1",    1.0, 0, 1, C_ACCENT)
        add_jog("+0.1",  0.1, 0, 2, C_ACCENT)
        add_jog("-10", -10.0, 1, 0, C_DANGER)
        add_jog("-1",   -1.0, 1, 1, C_DANGER)
        add_jog("-0.1", -0.1, 1, 2, C_DANGER)
        lay.addLayout(jog_grid)

        # 절대 이동 (Target + GO)
        row_abs = QHBoxLayout()
        self.spin_abs = QDoubleSpinBox()
        self.spin_abs.setRange(-10000.0, 10000.0)
        self.spin_abs.setSuffix(" um")
        self.spin_abs.setStyleSheet(SPIN_STYLE)
        
        self.btn_abs_move = QPushButton("GO")
        self.btn_abs_move.setStyleSheet(BTN_SMALL)
        self.btn_abs_move.setEnabled(False)
        self.btn_abs_move.clicked.connect(self._on_abs_move)
        self._move_btns.append(self.btn_abs_move)

        row_abs.addWidget(self.spin_abs, 1)
        row_abs.addWidget(self.btn_abs_move)
        lay.addLayout(row_abs)

    def _build_settings_group(self, lay: QVBoxLayout):
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        # Safety Limit
        row_lim = QHBoxLayout()
        lbl_lim = QLabel("Limit(um)")
        lbl_lim.setStyleSheet(lbl(C_TEXT_DIM))
        self.spin_limit = QDoubleSpinBox()
        self.spin_limit.setRange(0.0, 10000.0)
        self.spin_limit.setValue(10000.0)
        self.spin_limit.setStyleSheet(SPIN_STYLE)
        self.spin_limit.valueChanged.connect(self._on_limit_changed)
        row_lim.addWidget(lbl_lim)
        row_lim.addWidget(self.spin_limit)
        lay.addLayout(row_lim)

        # Velocity
        row_vel = QHBoxLayout()
        lbl_vel = QLabel("Vel(um/s)")
        lbl_vel.setStyleSheet(lbl(C_TEXT_DIM))
        self.spin_vel = QDoubleSpinBox()
        self.spin_vel.setRange(0.1, 100.0)
        self.spin_vel.setValue(10.0)
        self.spin_vel.setStyleSheet(SPIN_STYLE)
        self.spin_vel.valueChanged.connect(self._on_vel_changed)
        row_vel.addWidget(lbl_vel)
        row_vel.addWidget(self.spin_vel)
        lay.addLayout(row_vel)

        # Dry Run
        self.check_dry = QCheckBox("DRY RUN (Simulate Move)")
        self.check_dry.setStyleSheet(f"color:{C_WARN}; font-family:'{Fonts.UI}'; font-size:14px; font-weight:bold;")
        self.check_dry.toggled.connect(self._on_dry_run_changed)
        lay.addWidget(self.check_dry)

    # ── 연결 / 해제 ────────────────────────────────────────────────────

    def _on_connect(self):
        ip   = self.edit_ip.text().strip()
        port_str = self.edit_port.text().strip()
        if not ip or not port_str:
            self._log("KIMM: IP와 Port를 입력하세요")
            return
        try:
            port = int(port_str)
        except ValueError:
            self._log("KIMM: Port는 정수여야 합니다")
            return

        self._save_settings()
        self._ctrl = KIMMZController(ip, port)
        limit_val = self.spin_limit.value()
        self._ctrl.z_safety_limit = limit_val
        self._ctrl.z_lower_limit = -limit_val
        self._ctrl.default_velocity = self.spin_vel.value()
        self._ctrl.dry_run = self.check_dry.isChecked()
        self._log(f"KIMM: 연결 시도 → {ip}:{port}")

        try:
            self._ctrl.connect()
            self._log(f"KIMM: 연결 성공 (Limit={self._ctrl.z_safety_limit}um, Vel={self._ctrl.default_velocity}um/s)")
            self.lbl_status.setText("● CONNECTED")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.edit_ip.setEnabled(False)
            self.edit_port.setEnabled(False)
            for b in self._move_btns: b.setEnabled(True)
            self.kimm_connected.emit(self._ctrl)
            self._poll_timer.start()
        except Exception as e:
            self._log(f"KIMM: 연결 실패 — {e}")
            self._ctrl = None

    def _on_disconnect(self):
        self._poll_timer.stop()
        if self._ctrl:
            self._ctrl.disconnect()
            self._ctrl = None
        self.lbl_status.setText("● DISCONNECTED")
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        self.lbl_z.setText("---  um")
        self.lbl_servo.setText("OFF")
        self.lbl_servo.setStyleSheet(
            f"color:{C_TEXT_DIM}; font-family:'{Fonts.MONO}'; font-size:14px; font-weight:bold;"
        )
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.edit_ip.setEnabled(True)
        self.edit_port.setEnabled(True)
        for b in self._move_btns: b.setEnabled(False)
        self.kimm_disconnected.emit()
        self._log("KIMM: 연결 해제")


    # ── 폴링 ──────────────────────────────────────────────────────────

    def _poll(self):
        """100 ms마다 위치 요청 → 수신 루프에서 갱신된 값 표시."""
        if self._ctrl is None or not self._ctrl.is_connected:
            self._poll_timer.stop()
            self._on_disconnect()
            return

        self._ctrl.request_position()
        z = self._ctrl.current_z
        self.lbl_z.setText(f"{z:+.3f}  um")

        if self._ctrl.servo_on:
            self.lbl_servo.setText("ON")
            self.lbl_servo.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
        else:
            self.lbl_servo.setText("OFF")
            self.lbl_servo.setStyleSheet(lbl(C_TEXT_DEAD, mono=True, bold=True))

    def _on_jog(self, delta: float):
        if not self._ctrl or not self._ctrl.is_connected: return
        self._log(f"KIMM Jog: {delta:+.2f} um")
        
        def run():
            try:
                self._ctrl.move_by_z(delta)
            except Exception as e:
                self.log_message.emit(f"KIMM Jog 실패: {e}")
                
        threading.Thread(target=run, daemon=True).start()

    def _on_abs_move(self):
        if not self._ctrl or not self._ctrl.is_connected: return
        target = self.spin_abs.value()
        self._log(f"KIMM Move To: {target:.2f} um")
        
        def run():
            try:
                self._ctrl.move_to_z(target)
            except Exception as e:
                self.log_message.emit(f"KIMM Move To 실패: {e}")
                
        threading.Thread(target=run, daemon=True).start()

    def _on_limit_changed(self, val: float):
        if self._ctrl:
            self._ctrl.z_safety_limit = val
            self._ctrl.z_lower_limit = -val
        self._save_settings()

    def _on_vel_changed(self, val: float):
        if self._ctrl:
            self._ctrl.default_velocity = val
        self._save_settings()

    def _on_dry_run_changed(self, checked: bool):
        if self._ctrl:
            self._ctrl.dry_run = checked
        if checked:
            self._log("⚠️ KIMM: DRY RUN 모드 활성 — 실제 모터가 움직이지 않습니다.")
        else:
            self._log("▶ KIMM: DRY RUN 모드 해제 — 실제 하드웨어 명령이 전송됩니다.")
        self._save_settings()

    # ── 설정 저장/복원 ─────────────────────────────────────────────────

    def _save_settings(self):
        self._settings.setValue(_SETTINGS_KEY_IP,    self.edit_ip.text().strip())
        self._settings.setValue(_SETTINGS_KEY_PORT,  self.edit_port.text().strip())
        self._settings.setValue(_SETTINGS_KEY_LIMIT, self.spin_limit.value())
        self._settings.setValue(_SETTINGS_KEY_VEL,   self.spin_vel.value())
        self._settings.setValue(_SETTINGS_KEY_DRY,   self.check_dry.isChecked())

        # 섹션 상태 저장
        for sec in self._sections:
            key = f"kimm/sec_{sec._title_lbl.text().replace(' ', '_').lower()}_collapsed"
            self._settings.setValue(key, sec.is_collapsed())

    def _load_settings(self):
        self.edit_ip.setText(self._settings.value(_SETTINGS_KEY_IP,   "192.168.1.100"))
        self.edit_port.setText(self._settings.value(_SETTINGS_KEY_PORT, "5000"))
        self.spin_limit.setValue(float(self._settings.value(_SETTINGS_KEY_LIMIT, 10000.0)))
        self.spin_vel.setValue(float(self._settings.value(_SETTINGS_KEY_VEL, 10.0)))
        self.check_dry.setChecked(self._settings.value(_SETTINGS_KEY_DRY, False, type=bool))

        # 섹션 상태 복원
        for sec in self._sections:
            key = f"kimm/sec_{sec._title_lbl.text().replace(' ', '_').lower()}_collapsed"
            val = self._settings.value(key, None)
            if val is not None:
                sec.set_collapsed(str(val).lower() == 'true')

    # ── 로그 헬퍼 ─────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_message.emit(msg)
