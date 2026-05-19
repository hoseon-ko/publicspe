"""
ui/widgets/kimm_z_card.py
KIMM Z-Stage 전용 공통 제어 카드 위젯.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
    QPushButton, QFrame, QDoubleSpinBox, QLineEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from core.config import get_config
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_TEXT, C_TEXT_DIM,
    Fonts, BTN_SMALL, SPIN_STYLE, EDIT_STYLE, lbl
)
from ui.widgets.collapsible_section import CollapsibleSection

class KimmZCard(QFrame):
    log_message = pyqtSignal(str)
    set_center_requested = pyqtSignal(float) # AutoFocus용

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session_hub = None
        self._jog_btns: list[QPushButton] = []
        self._cfg = get_config()
        self.setObjectName("motionCard")
        
        # 스타일 적용 (MotionTab과 동일)
        self.setStyleSheet(f"""
            QFrame#motionCard {{
                background: #0f1729;
                border: 1px solid #11345f;
                border-radius: 6px;
            }}
            QFrame#motionSection {{
                background: #0b1222;
                border: 1px solid #13223d;
                border-radius: 4px;
            }}
        """)
        
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._refresh_status)
        
        self._build_ui()
        self._load_settings()

    def _section_box(self, title: str, accent: str) -> CollapsibleSection:
        return CollapsibleSection(title, accent=accent)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)
        
        # Title
        title = QLabel("▾  KIMM FINE STAGE")
        title.setStyleSheet(f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: 20px; font-weight: bold; letter-spacing: 2px;")
        lay.addWidget(title)

        # 1) Connection
        self.sec_conn = self._section_box("CONNECTION (Z)", C_ACCENT)
        conn_l = self.sec_conn.content_layout()
        
        row_ip = QHBoxLayout()
        lbl_ip = QLabel("IP")
        lbl_ip.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_ip.setFixedWidth(50)
        self.edit_ip = QLineEdit("192.168.1.100")
        self.edit_ip.editingFinished.connect(self._save_settings)
        self.edit_ip.setStyleSheet(EDIT_STYLE)
        row_ip.addWidget(lbl_ip)
        row_ip.addWidget(self.edit_ip)
        conn_l.addLayout(row_ip)

        row_port = QHBoxLayout()
        lbl_port = QLabel("PORT")
        lbl_port.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_port.setFixedWidth(50)
        self.edit_port = QLineEdit("5000")
        self.edit_port.editingFinished.connect(self._save_settings)
        self.edit_port.setStyleSheet(EDIT_STYLE)
        row_port.addWidget(lbl_port)
        row_port.addWidget(self.edit_port)
        conn_l.addLayout(row_port)

        btn_row = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_connect.setStyleSheet(BTN_SMALL)
        self.btn_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_disconnect.clicked.connect(self._on_disconnect_clicked)
        btn_row.addWidget(self.btn_connect)
        btn_row.addWidget(self.btn_disconnect)
        conn_l.addLayout(btn_row)
        
        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        conn_l.addWidget(self.lbl_status)
        lay.addWidget(self.sec_conn)

        # 2) Status & Position
        self.sec_stat = self._section_box("STATUS & POSITION", C_DANGER)
        stat_l = self.sec_stat.content_layout()
        
        # 대형 디스플레이
        disp_frame = QFrame()
        disp_frame.setObjectName("motionSection")
        disp_frame.setStyleSheet(self.styleSheet())
        disp_v = QVBoxLayout(disp_frame)
        self.lbl_z = QLabel("--- um")
        self.lbl_z.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_z.setStyleSheet(f"color:#d8e8ff; font-family:'{Fonts.MONO}'; font-size:24px; font-weight:bold; border:none;")
        disp_v.addWidget(self.lbl_z)
        
        self.lbl_axes = QLabel("All Axes: ---")
        self.lbl_axes.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_axes.setStyleSheet(f"color:{C_TEXT_DIM}; font-family:'{Fonts.MONO}'; font-size:10px; border:none;")
        disp_v.addWidget(self.lbl_axes)
        
        stat_l.addWidget(disp_frame)
        
        # 상태 레이블
        stat_row = QHBoxLayout()
        self.lbl_servo = QLabel("SERVO: OFF")
        self.lbl_limit = QLabel("LIMIT: --")
        self.lbl_vel   = QLabel("VEL: --")
        for w in (self.lbl_servo, self.lbl_limit, self.lbl_vel):
            w.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            stat_row.addWidget(w)
        stat_l.addLayout(stat_row)
        lay.addWidget(self.sec_stat)

        # 3) Manual Control
        self.sec_ctrl = self._section_box("MANUAL CONTROL", C_ACCENT)
        ctrl_l = self.sec_ctrl.content_layout()
        
        jog_grid = QGridLayout()
        jog_grid.setSpacing(4)
        specs = [("+10", 10.0, 0, 0), ("+1", 1.0, 0, 1), ("+0.1", 0.1, 0, 2),
                 ("-10", -10.0, 1, 0), ("-1", -1.0, 1, 1), ("-0.1", -0.1, 1, 2)]
        for text, d, r, c in specs:
            btn = QPushButton(text)
            color = C_ACCENT if d > 0 else C_DANGER
            btn.setStyleSheet(BTN_SMALL.replace(C_ACCENT, color))
            btn.clicked.connect(lambda _, val=d: self._on_jog_clicked(val))
            jog_grid.addWidget(btn, r, c)
            self._jog_btns.append(btn)
        ctrl_l.addLayout(jog_grid)
        
        abs_row = QHBoxLayout()
        self.spin_abs = QDoubleSpinBox()
        self.spin_abs.setRange(-100000, 100000)
        self.spin_abs.setDecimals(3)
        self.spin_abs.setSuffix(" um")
        self.spin_abs.setStyleSheet(SPIN_STYLE)
        
        self.btn_go = QPushButton("GO")
        self.btn_go.setFixedWidth(40)
        self.btn_go.setStyleSheet(BTN_SMALL)
        self.btn_go.clicked.connect(self._on_go_clicked)

        abs_row.addWidget(self.spin_abs, 1)
        abs_row.addWidget(self.btn_go)
        ctrl_l.addLayout(abs_row)
        lay.addWidget(self.sec_ctrl)
        
        lay.addStretch()
        self.update_status(False, None)

        # NOTE: edit_ip/edit_port 는 line 73, 84 에서 이미 editingFinished 로 연결됨.
        # textChanged 로 추가 연결하면 _load_settings 의 setText() 가 startup 마다
        # _save_settings 를 발화시켜 settings.json 을 덮어쓴다.

    def bind_session_hub(self, hub):
        if self._session_hub:
            try:
                self._session_hub.event_published.disconnect(self._on_session_event)
            except Exception:
                pass
        self._session_hub = hub
        if hub:
            hub.event_published.connect(self._on_session_event)
            self._poll_timer.start()
            self._refresh_status()
        else:
            self._poll_timer.stop()

    def _on_session_event(self, event):
        from core.session.session_events import SessionEventType
        if event.event_type == SessionEventType.KIMM_CONNECTED:
            self._load_settings()  # 실시간 타 탭 입력 동기화
            self._refresh_status()
        elif event.event_type == SessionEventType.KIMM_DISCONNECTED:
            self.update_status(False, None, False)

    def _refresh_status(self):
        """내부 타이머에 의한 자체 폴링 (세션 허브가 직접 연결된 경우)"""
        if not self._session_hub: return
        try:
            connected = False
            if hasattr(self._session_hub, 'is_kimm_connected'):
                connected = bool(self._session_hub.is_kimm_connected())
            
            if connected:
                z = self._session_hub.kimm_get_z()
            else:
                z = None
            self.update_status(connected, z, False)
        except Exception as e:
            self.update_status(False, None, False)

    def update_status(self, connected: bool, z: float | None, sim_mode: bool = False):
        """외부(MotionTab 등)에서 폴링된 데이터를 주입받아 UI 갱신"""
        if connected:
            self.lbl_status.setText("● CONNECTED")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
        elif sim_mode:
            self.lbl_status.setText("● SIMULATING")
            self.lbl_status.setStyleSheet(lbl(C_WARN, mono=True, bold=True))
        else:
            self.lbl_status.setText("● DISCONNECTED")
            self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))

        self.lbl_z.setText(f"{z:+.3f} um" if z is not None else "--- um")
        
        # 6축 데이터 표시
        if connected and self._session_hub:
            ctrl = self._session_hub.kimm_controller
            if ctrl and hasattr(ctrl, '_positions'):
                p = ctrl._positions
                self.lbl_axes.setText(
                    f"X(1): {p[0]:+.2f} | Y(2): {p[1]:+.2f} | Z(3): {p[2]:+.2f}\n"
                    f"Tx(4): {p[3]:+.2f} | Ty(5): {p[4]:+.2f} | Tz(6): {p[5]:+.2f}"
                )
            else:
                self.lbl_axes.setText("All Axes: N/A")
        else:
            self.lbl_axes.setText("All Axes: ---")
        
        # 버튼 활성화 제어
        active = bool(connected or sim_mode)
        self.btn_connect.setEnabled(not active)
        self.btn_disconnect.setEnabled(active)
        self.edit_ip.setEnabled(not active)
        self.edit_port.setEnabled(not active)
        
        for btn in self._jog_btns:
            btn.setEnabled(active)
        self.spin_abs.setEnabled(active)
        self.btn_go.setEnabled(active)

    def _on_connect_clicked(self):
        if self._session_hub:
            self._save_settings()  # 연결 성공 기록용 저장
            ip = self.edit_ip.text().strip()
            port_str = self.edit_port.text().strip()
            port = int(port_str) if port_str else 5000
            try:
                self.log_message.emit(f"KIMM Fine Stage: Connecting to {ip}:{port}...")
                self._session_hub.kimm_connect(ip, port)
                self.log_message.emit(f"KIMM Fine Stage: Connected to {ip}:{port} successfully.")
            except Exception as e:
                self.log_message.emit(f"KIMM Fine Stage: Connection to {ip}:{port} failed: {e}")

    def _on_disconnect_clicked(self):
        if self._session_hub:
            try:
                self.log_message.emit("KIMM Fine Stage: Disconnecting...")
                self._session_hub.kimm_disconnect()
                self.log_message.emit("KIMM Fine Stage: Disconnected successfully.")
                self.update_status(False, None, False)
            except Exception as e:
                self.log_message.emit(f"KIMM Fine Stage: Disconnect failed: {e}")
                self.update_status(False, None, False)

    def _on_jog_clicked(self, delta: float):
        if self._session_hub:
            import threading
            def run():
                try:
                    cur = self._session_hub.kimm_get_z()
                    self._session_hub.kimm_move_to_z(cur + delta)
                except Exception as e:
                    self.log_message.emit(f"KIMM Jog failed: {e}")
            threading.Thread(target=run, daemon=True).start()

    def _on_go_clicked(self):
        if self._session_hub:
            import threading
            target_val = self.spin_abs.value()
            def run():
                try:
                    self._session_hub.kimm_move_to_z(target_val)
                except Exception as e:
                    self.log_message.emit(f"KIMM Go failed: {e}")
            threading.Thread(target=run, daemon=True).start()

    def _save_settings(self):
        c = self._cfg
        c.set("devices.kimm.ip",   self.edit_ip.text().strip())
        c.set("devices.kimm.port", self.edit_port.text().strip())
        c.save()

    def _load_settings(self):
        c = self._cfg
        self.edit_ip.setText(str(c.get("devices.kimm.ip",   "192.168.1.100")))
        self.edit_port.setText(str(c.get("devices.kimm.port", "5000")))


