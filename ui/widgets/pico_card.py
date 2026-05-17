"""
ui/widgets/pico_card.py
Picomotor 4축 통합 제어 카드 위젯.
"""

from __future__ import annotations
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
    QPushButton, QFrame, QLineEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_TEXT, C_TEXT_DIM,
    Fonts, BTN_SMALL, EDIT_STYLE, lbl
)
from ui.widgets.collapsible_section import CollapsibleSection
from ui.widgets.motor_card import MotorCard

class PicoCard(QFrame):
    log_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session_hub = None
        self.setObjectName("motionCard")
        
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
        
        self._pico_cards: list[MotorCard] = []
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self.refresh_status)
        
        self._build_ui()

    def _section_box(self, title: str, accent: str) -> CollapsibleSection:
        sec = CollapsibleSection(title, accent=accent)
        sec.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        return sec

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)
        
        # Title
        title = QLabel("▾  PICOMOTOR 8742")
        title.setStyleSheet(f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: 20px; font-weight: bold; letter-spacing: 2px;")
        lay.addWidget(title)

        # 1) Connection
        self.sec_conn = self._section_box("CONNECTION (USB/LAN)", C_ACCENT)
        conn_l = self.sec_conn.content_layout()
        
        status_row = QHBoxLayout()
        self.lbl_pico_model = QLabel("USB link idle")
        self.lbl_pico_model.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        self.lbl_pico_status = QLabel("● DISCONNECTED")
        self.lbl_pico_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pico_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        status_row.addWidget(self.lbl_pico_model, 1)
        status_row.addWidget(self.lbl_pico_status, 1)
        conn_l.addLayout(status_row)

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
        lay.addWidget(self.sec_conn)

        # 2) Axis Controls
        self.sec_ctrl = self._section_box("AXIS CONTROLS", C_ACCENT)
        ctrl_l = self.sec_ctrl.content_layout()
        
        card_grid = QGridLayout()
        card_grid.setSpacing(4)
        for idx in range(4):
            motor_card = MotorCard(idx + 1)
            motor_card.setMinimumHeight(130)
            motor_card.setMaximumHeight(148)
            motor_card.move_requested.connect(self._on_move_requested)
            self._pico_cards.append(motor_card)
            card_grid.addWidget(motor_card, idx // 2, idx % 2)
        ctrl_l.addLayout(card_grid)
        lay.addWidget(self.sec_ctrl)

        # 3) Global Actions
        btn_row_global = QHBoxLayout()
        self.btn_zero_all = QPushButton("ZERO ALL")
        self.btn_stop_all = QPushButton("STOP ALL")
        self.btn_refresh = QPushButton("REFRESH")
        self.btn_zero_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        self.btn_stop_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_refresh.setStyleSheet(BTN_SMALL)
        self.btn_zero_all.clicked.connect(self._on_zero_all_clicked)
        self.btn_stop_all.clicked.connect(self._on_stop_all_clicked)
        self.btn_refresh.clicked.connect(self.refresh_status)
        btn_row_global.addWidget(self.btn_zero_all)
        btn_row_global.addWidget(self.btn_stop_all)
        btn_row_global.addWidget(self.btn_refresh)
        lay.addLayout(btn_row_global)
        
        lay.addStretch()

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
            self.refresh_status()
        else:
            self._poll_timer.stop()

    def _on_session_event(self, event):
        from core.session.session_events import SessionEventType
        if event.event_type == SessionEventType.PICO_CONNECTED:
            self.refresh_status()
        elif event.event_type == SessionEventType.PICO_DISCONNECTED:
            self.update_status(False, [None] * 4)

    def refresh_status(self):
        if not self._session_hub: return
        try:
            connected = self._session_hub.is_pico_connected()
            if connected:
                positions = [self._session_hub.pico_get_position(ax) for ax in range(1, 5)]
            else:
                positions = [None, None, None, None]
            self.update_status(connected, positions)
        except Exception as e:
            self.log_message.emit(f"PICO Status Refresh Error: {e}")

    def update_status(self, connected: bool, positions: list[Optional[int]]):
        if connected:
            self.lbl_pico_status.setText("● CONNECTED")
            self.lbl_pico_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.lbl_pico_model.setText("USB/LAN Active")
        else:
            self.lbl_pico_status.setText("● DISCONNECTED")
            self.lbl_pico_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
            self.lbl_pico_model.setText("USB link idle")

        for i, card in enumerate(self._pico_cards):
            card.set_enabled(connected)
            if connected and i < len(positions):
                card.set_position(positions[i])
            else:
                card.set_position(None)

        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        self.btn_zero_all.setEnabled(connected)
        self.btn_stop_all.setEnabled(connected)

    def _on_connect_clicked(self):
        if self._session_hub:
            try:
                self.log_message.emit("PICO Mirror: Connecting...")
                self._session_hub.connect_pico()
                self._session_hub.start_pico_polling()
                self.log_message.emit("PICO Mirror: Connected successfully.")
            except Exception as e:
                self.log_message.emit(f"PICO Mirror: Connection failed: {e}")
                self.update_status(False, [None] * 4)

    def _on_disconnect_clicked(self):
        if self._session_hub:
            try:
                self.log_message.emit("PICO Mirror: Disconnecting...")
                self._session_hub.disconnect_pico()
                self.log_message.emit("PICO Mirror: Disconnected successfully.")
                self.update_status(False, [None] * 4)
            except Exception as e:
                self.log_message.emit(f"PICO Mirror: Disconnect failed: {e}")
                self.update_status(False, [None] * 4)

    def _on_move_requested(self, motor_num: int, steps: int):
        if not self._session_hub: return
        try:
            if steps == 0:
                self._session_hub.pico_zero(motor_num)
            else:
                self._session_hub.pico_move_relative(motor_num, steps)
                # 피드백을 위해 flash 호출
                self._pico_cards[motor_num-1].flash_moving(steps)
        except Exception as e:
            self.log_message.emit(f"PICO M{motor_num} Move Error: {e}")

    def _on_zero_all_clicked(self):
        if self._session_hub:
            for i in range(1, 5):
                self._session_hub.pico_zero(i)

    def _on_stop_all_clicked(self):
        if self._session_hub:
            self._session_hub.pico_stop_all()
