"""
ui/live/kimm_z_panel.py
KIMM Fine Stage Z축 연결 + 위치 조회 패널 — SpeAnalyze 다크 테마.

기능:
  - IP / Port 입력 후 Connect / Disconnect
  - 100 ms 폴링으로 Z 위치 표시
  - Servo 상태 표시
  - 이동 기능은 위치 확인 후 별도 추가 예정
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFrame, QGroupBox,
    QDoubleSpinBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal

from core.motor.kimm_z import KIMMZController

# ── 스타일 토큰 ────────────────────────────────────────────────────────────────
_FC = "Courier New"

_CARD_STYLE = """
    QFrame#kimmCard {
        background: #0f1729;
        border: 1px solid #0f3460;
        border-radius: 6px;
    }
"""
_EDIT_STYLE = """
    QLineEdit {
        background: #080e1e; border: 1px solid #0f3460;
        color: #c0d0ff; border-radius: 3px;
        font-family: 'Courier New'; font-size: 11px; padding: 2px 6px;
    }
"""
_GRP_STYLE = """
    QGroupBox {{
        border: 1px solid {color}; border-radius: 6px;
        margin-top: 10px; font-family: 'Courier New';
        font-size: 11px; color: {color};
        letter-spacing: 2px; font-weight: bold;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
"""

_SETTINGS_KEY_IP     = "kimm/ip"
_SETTINGS_KEY_PORT   = "kimm/port"
_SETTINGS_KEY_LIMIT  = "kimm/safety_limit"
_SETTINGS_KEY_VEL    = "kimm/velocity"
_SETTINGS_KEY_DRY    = "kimm/dry_run"


class KIMMZPanel(QWidget):
    """KIMM Z축 위치 표시 + 연결 관리 패널."""

    log_message = pyqtSignal(str)   # 상위 시스템 로그로 전달

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctrl: KIMMZController | None = None
        self._settings = QSettings("SpeAnalyze", "MainWindow")

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll)

        self._build_ui()
        self._load_settings()

    # ── UI 구성 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── 연결 그룹 ──────────────────────────────────────────────────
        grp_conn = QGroupBox("CONNECTION")
        grp_conn.setStyleSheet(_GRP_STYLE.format(color="#4ecdc4"))
        conn_layout = QVBoxLayout(grp_conn)
        conn_layout.setSpacing(4)

        # IP 입력
        row_ip = QHBoxLayout()
        lbl_ip = QLabel("IP")
        lbl_ip.setStyleSheet(f"color:#8090b0; font-family:'{_FC}'; font-size:11px;")
        lbl_ip.setFixedWidth(28)
        self.edit_ip = QLineEdit()
        self.edit_ip.setPlaceholderText("192.168.1.100")
        self.edit_ip.setStyleSheet(_EDIT_STYLE)
        row_ip.addWidget(lbl_ip)
        row_ip.addWidget(self.edit_ip)
        conn_layout.addLayout(row_ip)

        # Port 입력
        row_port = QHBoxLayout()
        lbl_port = QLabel("PORT")
        lbl_port.setStyleSheet(f"color:#8090b0; font-family:'{_FC}'; font-size:11px;")
        lbl_port.setFixedWidth(28)
        self.edit_port = QLineEdit()
        self.edit_port.setPlaceholderText("5000")
        self.edit_port.setStyleSheet(_EDIT_STYLE)
        self.edit_port.setFixedWidth(60)
        row_port.addWidget(lbl_port)
        row_port.addWidget(self.edit_port)
        row_port.addStretch()
        conn_layout.addLayout(row_port)

        # Connect / Disconnect 버튼
        row_btn = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setStyleSheet(self._btn_style("#4ecdc4"))
        self.btn_connect.clicked.connect(self._on_connect)

        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_disconnect.setStyleSheet(self._btn_style("#e94560"))
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.clicked.connect(self._on_disconnect)

        row_btn.addWidget(self.btn_connect)
        row_btn.addWidget(self.btn_disconnect)
        conn_layout.addLayout(row_btn)

        # 연결 상태 표시
        self.lbl_conn_status = QLabel("● DISCONNECTED")
        self.lbl_conn_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_conn_status.setStyleSheet(
            f"color:#e94560; font-family:'{_FC}'; font-size:11px; font-weight:bold;"
        )
        conn_layout.addWidget(self.lbl_conn_status)
        root.addWidget(grp_conn)

        # ── 위치 표시 그룹 ─────────────────────────────────────────────
        grp_pos = QGroupBox("Z  POSITION")
        grp_pos.setStyleSheet(_GRP_STYLE.format(color="#e94560"))
        pos_layout = QVBoxLayout(grp_pos)
        pos_layout.setSpacing(6)

        # Z 위치 (큰 숫자)
        self.lbl_z = QLabel("---  um")
        self.lbl_z.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_z.setStyleSheet(
            f"color:#d8e8ff; font-family:'{_FC}'; font-size:22px; font-weight:bold;"
        )
        pos_layout.addWidget(self.lbl_z)

        # Servo 상태
        row_servo = QHBoxLayout()
        lbl_s = QLabel("SERVO")
        lbl_s.setStyleSheet(f"color:#8090b0; font-family:'{_FC}'; font-size:11px;")
        self.lbl_servo = QLabel("OFF")
        self.lbl_servo.setStyleSheet(
            f"color:#4a5a7a; font-family:'{_FC}'; font-size:11px; font-weight:bold;"
        )
        row_servo.addWidget(lbl_s)
        row_servo.addStretch()
        row_servo.addWidget(self.lbl_servo)
        pos_layout.addLayout(row_servo)

        root.addWidget(grp_pos)

        # ── 설정 그룹 (리밋/속도) ──────────────────────────────────────
        grp_set = QGroupBox("SETTINGS")
        grp_set.setStyleSheet(_GRP_STYLE.format(color="#9a6a4a"))
        set_layout = QVBoxLayout(grp_set)
        set_layout.setSpacing(4)

        # Safety Limit
        row_lim = QHBoxLayout()
        lbl_lim = QLabel("Limit(um)")
        lbl_lim.setStyleSheet(f"color:#8090b0; font-family:'{_FC}'; font-size:11px;")
        lbl_lim.setFixedWidth(65)
        self.spin_limit = QDoubleSpinBox()
        self.spin_limit.setRange(-1000.0, 1000.0)
        self.spin_limit.setValue(0.0)
        self.spin_limit.setDecimals(1)
        self.spin_limit.setStyleSheet(self._spin_style())
        self.spin_limit.valueChanged.connect(self._on_limit_changed)
        row_lim.addWidget(lbl_lim)
        row_lim.addWidget(self.spin_limit, 1)
        set_layout.addLayout(row_lim)

        # Velocity
        row_vel = QHBoxLayout()
        lbl_vel = QLabel("Vel(um/s)")
        lbl_vel.setStyleSheet(f"color:#8090b0; font-family:'{_FC}'; font-size:11px;")
        lbl_vel.setFixedWidth(65)
        self.spin_vel = QDoubleSpinBox()
        self.spin_vel.setRange(0.1, 100.0)
        self.spin_vel.setValue(10.0)
        self.spin_vel.setDecimals(1)
        self.spin_vel.setStyleSheet(self._spin_style())
        self.spin_vel.valueChanged.connect(self._on_vel_changed)
        row_vel.addWidget(lbl_vel)
        row_vel.addWidget(self.spin_vel, 1)
        set_layout.addLayout(row_vel)

        # Dry Run
        self.check_dry = QCheckBox("DRY RUN (Simulate Move)")
        self.check_dry.setStyleSheet("""
            QCheckBox { color: #ffe66d; font-family: 'Courier New'; font-size: 11px; font-weight: bold; }
        """)
        self.check_dry.toggled.connect(self._on_dry_run_changed)
        set_layout.addWidget(self.check_dry)

        root.addWidget(grp_set)
        root.addStretch()

    # ── 버튼 스타일 헬퍼 ──────────────────────────────────────────────

    @staticmethod
    def _spin_style() -> str:
        return """
            QDoubleSpinBox {
                background: #080e1e; border: 1px solid #0f3460;
                color: #c0d0ff; border-radius: 3px;
                font-family: 'Courier New'; font-size: 11px; padding: 1px 4px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; }
        """

    @staticmethod
    def _btn_style(color: str) -> str:
        return f"""
            QPushButton {{
                background: transparent; color: {color};
                border: 1px solid {color}; border-radius: 3px;
                font-family: 'Courier New'; font-size: 11px;
                font-weight: bold; padding: 3px 8px;
            }}
            QPushButton:hover {{ background: {color}22; }}
            QPushButton:disabled {{ color: #304060; border-color: #1a2840; }}
        """

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
        self._ctrl.z_safety_limit = self.spin_limit.value()
        self._ctrl.default_velocity = self.spin_vel.value()
        self._ctrl.dry_run = self.check_dry.isChecked()
        self._log(f"KIMM: 연결 시도 → {ip}:{port}")

        if self._ctrl.connect():
            self._log(f"KIMM: 연결 성공 (Limit={self._ctrl.z_safety_limit}um, Vel={self._ctrl.default_velocity}um/s)")
            self.lbl_conn_status.setText("● CONNECTED")
            self.lbl_conn_status.setStyleSheet(
                f"color:#4ecdc4; font-family:'Courier New'; font-size:11px; font-weight:bold;"
            )
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.edit_ip.setEnabled(False)
            self.edit_port.setEnabled(False)
            self._poll_timer.start()
        else:
            self._log("KIMM: 연결 실패 — IP/Port 확인")
            self._ctrl = None

    def _on_disconnect(self):
        self._poll_timer.stop()
        if self._ctrl:
            self._ctrl.disconnect()
            self._ctrl = None
        self.lbl_conn_status.setText("● DISCONNECTED")
        self.lbl_conn_status.setStyleSheet(
            f"color:#e94560; font-family:'Courier New'; font-size:11px; font-weight:bold;"
        )
        self.lbl_z.setText("---  um")
        self.lbl_servo.setText("OFF")
        self.lbl_servo.setStyleSheet(
            f"color:#4a5a7a; font-family:'Courier New'; font-size:11px; font-weight:bold;"
        )
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.edit_ip.setEnabled(True)
        self.edit_port.setEnabled(True)
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
            self.lbl_servo.setStyleSheet(
                f"color:#4ecdc4; font-family:'Courier New'; font-size:11px; font-weight:bold;"
            )
        else:
            self.lbl_servo.setText("OFF")
            self.lbl_servo.setStyleSheet(
                f"color:#4a5a7a; font-family:'Courier New'; font-size:11px; font-weight:bold;"
            )

    def _on_limit_changed(self, val: float):
        if self._ctrl:
            self._ctrl.z_safety_limit = val
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

    def _load_settings(self):
        self.edit_ip.setText(self._settings.value(_SETTINGS_KEY_IP,   "192.168.1.100"))
        self.edit_port.setText(self._settings.value(_SETTINGS_KEY_PORT, "5000"))
        self.spin_limit.setValue(float(self._settings.value(_SETTINGS_KEY_LIMIT, 0.0)))
        self.spin_vel.setValue(float(self._settings.value(_SETTINGS_KEY_VEL, 10.0)))
        self.check_dry.setChecked(self._settings.value(_SETTINGS_KEY_DRY, False, type=bool))

    # ── 로그 헬퍼 ─────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_message.emit(msg)
