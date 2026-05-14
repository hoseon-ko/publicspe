"""
ui/motion/motion_tab.py
Motion dashboard tab for Picomotor / KIMM Z / ACS kinematic control.

Version: MotionTab v1.0 | Updated: 2026-05-12
"""

from __future__ import annotations

import threading
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QDoubleSpinBox, QLineEdit, QTextEdit, QSpinBox,
    QSizePolicy, QCheckBox, QLayout, QComboBox,
)

from core.motor.kinematic_calc import KinematicCalc, is_available as kinematic_available
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_BG_DEEP, C_BG_DARK, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD,
    Fonts, BTN_SMALL, SPIN_STYLE, EDIT_STYLE, CHECKBOX_STYLE, lbl,
)
from ui.live.motor_panel import MotorCard
from ui.widgets.collapsible_section import CollapsibleSection
from core.v2.motion.hybrid_hub import HybridMotionHubV2
from core.v2.motion.engine import MotionState, MotionResult
from core.v2.drivers.acs_adapter_v2 import AcsAdapterV2
from core.v2.drivers.kimm_adapter_v2 import KimmAdapterV2
from core.v2.drivers.pico_adapter_v2 import PicoAdapterV2


_CARD_BG = "#0f1729"
_CARD_BORDER = "#11345f"
_PANEL_BG = "#080e1e"


def _pill_style(color: str) -> str:
    return (
        f"color: {color}; font-family: '{Fonts.MONO}'; font-size: 11px; "
        "font-weight: bold; border: 1px solid "
        f"{color}; border-radius: 4px; padding: 2px 6px; background: rgba(255,255,255,0.02);"
        " min-height: 20px; max-height: 26px;"
    )


def _card_frame() -> str:
    return f"""
        QFrame#motionCard {{
            background: {_CARD_BG};
            border: 1px solid {_CARD_BORDER};
            border-radius: 6px;
        }}
        QFrame#motionSection {{
            background: #0b1222;
            border: 1px solid #13223d;
            border-radius: 4px;
        }}
    """


def _section() -> QFrame:
    frame = QFrame()
    frame.setObjectName("motionSection")
    frame.setStyleSheet(_card_frame())
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return frame


def _section_title(text: str, note: str = "") -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    title = QLabel(text)
    title.setStyleSheet(lbl(C_TEXT, mono=True, bold=True))
    _fix_h(title, 20)
    row.addWidget(title)
    if note:
        hint = QLabel(note)
        hint.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(hint, 20)
        row.addWidget(hint, 1)
    else:
        row.addStretch(1)
    return row


def _fix_h(widget: QWidget, height: int):
    widget.setMinimumHeight(height)
    widget.setMaximumHeight(height)


def _card_title(text: str, accent: str = C_ACCENT) -> QLabel:
    title = QLabel(f"▾  {text}")
    title.setStyleSheet(
        f"color: {accent}; font-family: '{Fonts.MONO}'; font-size: 20px; "
        "font-weight: bold; letter-spacing: 2px;"
    )
    _fix_h(title, 30)
    return title


def _section_box(title: str, accent: str = C_ACCENT) -> CollapsibleSection:
    sec = CollapsibleSection(title, accent=accent)
    sec.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return sec


class MotionTab(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._live_tab = None
        self._calc = KinematicCalc()
        self._pico_positions: list[Optional[int]] = [None, None, None, None]
        self._acs_positions: list[float] = [0.0] * 6
        self._acs_states: list[dict] = [{"enabled": False, "moving": False, "in_pos": False} for _ in range(6)]
        self._kimm_z: Optional[float] = None
        self._last_cal_pos = None
        self._log_lines: list[str] = []
        self._dof_order = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
        self._motor_axis_names = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
        self._dof_actual_labels: dict[str, QLabel] = {}
        self._dof_step_spins: dict[str, QDoubleSpinBox] = {}
        self._limit_plus_spins: list[QDoubleSpinBox] = []
        self._limit_minus_spins: list[QDoubleSpinBox] = []
        self._dir_combos: list[QComboBox] = []
        self._map_combos: list[QComboBox] = []
        self._stage_setup_spins: list[QDoubleSpinBox] = []
        self._encoder_pos_spins: list[QDoubleSpinBox] = []
        self._settings = QSettings("SpeAnalyze", "MainWindow")
        self._default_stage_setup = self._calc.stage_setup.copy()
        self._default_encoder_pos = self._calc.encoder_pos.copy()
        self._default_plus_limits = self._calc.plus_limits.copy()
        self._default_minus_limits = self._calc.minus_limits.copy()
        self._default_direction = self._calc.direction.copy()
        self._default_mapping = self._calc._mapping.copy()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._refresh_from_sources)
        self.log_message.connect(self._record_log_message)

        # V2 Hybrid Motion Hub Integration
        self._hub = HybridMotionHubV2()
        self._hub.any_busy_changed.connect(self._on_hub_busy_changed)
        self._hub.global_state_changed.connect(self._on_hub_state_summary)
        self._hub.emergency_occurred.connect(lambda msg: self.log_message.emit(f"!!! {msg} !!!"))

        self._build_ui()
        self._refresh_timer.start()

    # ------------------------------------------------------------------
    # Binding
    # ------------------------------------------------------------------

    def bind_live_tab(self, live_tab):
        self._live_tab = live_tab
        self._sync_connection_fields_from_live()
        self._refresh_from_sources()

    def cleanup(self):
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()

    # ── DeepAlign Master Bar 공개 API ─────────────────────────────────────────

    def refresh_positions(self) -> None:
        """현재 위치 새로 고침. Master Bar에서 호출."""
        self._refresh_from_sources()

    def stop_all_motion(self) -> None:
        """모든 모션 장치 즉시 정지. Master Bar에서 호출."""
        self._all_stop()

    def reconnect_all_devices(self) -> None:
        """모든 모션 장치 재연결 시도. Master Bar에서 호출."""
        self._reconnect_all()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; background: #0a0f1e; }")
        root.addWidget(scroll)

        body = QWidget()
        body.setObjectName("motionBody")
        body.setStyleSheet("QWidget#motionBody { background: #0a0f1e; }")
        self._body = body
        scroll.setWidget(body)

        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(8, 8, 8, 8)
        body_v.setSpacing(8)
        body_v.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)

        self._build_header(body_v)
        self._build_link_strip(body_v)
        self._build_cards(body_v)
        self._build_footer(body_v)
        body_v.addStretch(1)

    def _build_header(self, lay: QVBoxLayout):
        hdr = QFrame()
        hdr.setObjectName("motionCard")
        hdr.setStyleSheet(_card_frame())
        _fix_h(hdr, 58)
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(10, 6, 10, 6)
        hdr_l.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.lbl_title = QLabel("MOTIONTAB")
        self.lbl_title.setStyleSheet(
            f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: 20px; font-weight: bold;"
        )
        self.lbl_subtitle = QLabel("Unified motion control dashboard")
        self.lbl_subtitle.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(self.lbl_title, 25)
        _fix_h(self.lbl_subtitle, 20)
        title_box.addWidget(self.lbl_title)
        title_box.addWidget(self.lbl_subtitle)
        hdr_l.addLayout(title_box, 1)

        stat_wrap = QHBoxLayout()
        stat_wrap.setSpacing(6)
        self.lbl_hdr_pico_link = QLabel("Picomotor: --")
        self.lbl_hdr_kimm_link = QLabel("KIMM Z: --")
        self.lbl_hdr_acs_link = QLabel("ACS: --")
        for lblw in (self.lbl_hdr_pico_link, self.lbl_hdr_kimm_link, self.lbl_hdr_acs_link):
            lblw.setStyleSheet(_pill_style(C_TEXT_DIM))
            _fix_h(lblw, 30)
            stat_wrap.addWidget(lblw)
        hdr_l.addLayout(stat_wrap)

        self.check_global_sim = QCheckBox("SIMULATION MODE")
        self.check_global_sim.setStyleSheet(
            f"QCheckBox {{ color:{C_WARN}; font-family:'{Fonts.MONO}'; font-size:12px; font-weight:bold; "
            "margin-right: 10px; }"
        )
        self.check_global_sim.toggled.connect(self._on_global_sim_toggled)
        hdr_l.addWidget(self.check_global_sim)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setStyleSheet(BTN_SMALL)
        self.btn_refresh.clicked.connect(self._refresh_from_sources)

        self.btn_reconnect = QPushButton("Reconnect All")
        self.btn_reconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        self.btn_reconnect.clicked.connect(self._reconnect_all)

        self.btn_all_stop = QPushButton("All Stop")
        self.btn_all_stop.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_all_stop.clicked.connect(self._all_stop)

        for btn in (self.btn_refresh, self.btn_reconnect, self.btn_all_stop):
            _fix_h(btn, 32)
            hdr_l.addWidget(btn)

        lay.addWidget(hdr)

    def _build_link_strip(self, lay: QVBoxLayout):
        strip = QFrame()
        strip.setObjectName("motionCard")
        strip.setStyleSheet(_card_frame())
        _fix_h(strip, 52)
        row = QHBoxLayout(strip)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)

        self.lbl_strip_pico = self._strip_item("Picomotor Link", "USB idle / 4 CH")
        self.lbl_strip_kimm = self._strip_item("KIMM Link", "TCP idle / Z Axis")
        self.lbl_strip_acs = self._strip_item("ACS Link", "Ethernet or SIM / 6 Axis")
        self.lbl_strip_safety = self._strip_item("Global Safety", "Stop-first control path")
        for item in (self.lbl_strip_pico, self.lbl_strip_kimm, self.lbl_strip_acs, self.lbl_strip_safety):
            row.addWidget(item, 1)
        lay.addWidget(strip)

    def _strip_item(self, key: str, value: str) -> QFrame:
        item = _section()
        item.setMinimumHeight(40)
        item.setMaximumHeight(40)
        box = QVBoxLayout(item)
        box.setContentsMargins(8, 3, 8, 3)
        box.setSpacing(0)
        k = QLabel(key)
        v = QLabel(value)
        k.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        v.setStyleSheet(lbl(C_TEXT, mono=True, bold=True))
        _fix_h(k, 16)
        _fix_h(v, 18)
        box.addWidget(k)
        box.addWidget(v)
        item.value_label = v
        return item

    def _build_cards(self, lay: QVBoxLayout):
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)
        self._cards_grid = grid

        self.pico_card = self._build_pico_card()
        self.kimm_card = self._build_kimm_card()
        self.acs_card = self._build_acs_card()
        self._motion_cards = [self.pico_card, self.kimm_card, self.acs_card]
        self._relayout_cards()
        lay.addLayout(grid, 1)

    def _relayout_cards(self):
        if not hasattr(self, "_cards_grid"):
            return
        grid = self._cards_grid
        while grid.count():
            item = grid.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)

        w = max(self.width(), self._body.width() if hasattr(self, "_body") else 0)
        if w >= 1850:
            cols = 3
        elif w >= 1250:
            cols = 2
        else:
            cols = 1

        for c in range(3):
            grid.setColumnStretch(c, 0)
        for c in range(cols):
            grid.setColumnStretch(c, 1)

        for i, card in enumerate(self._motion_cards):
            r = i // cols
            c = i % cols
            grid.addWidget(card, r, c, alignment=Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_cards()

    def _build_footer(self, lay: QVBoxLayout):
        foot = QFrame()
        foot.setObjectName("motionCard")
        foot.setStyleSheet(_card_frame())
        _fix_h(foot, 154)
        foot_l = QHBoxLayout(foot)
        foot_l.setContentsMargins(8, 8, 8, 8)
        foot_l.setSpacing(8)

        log_panel = _section()
        log_l = QVBoxLayout(log_panel)
        log_l.setContentsMargins(8, 6, 8, 6)
        log_l.setSpacing(4)
        row = QHBoxLayout()
        title = QLabel("Recent Motion Log")
        title.setStyleSheet(lbl(C_TEXT, mono=True, bold=True))
        self.lbl_summary = QLabel("Ready")
        self.lbl_summary.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        self.lbl_counts = QLabel("0/3 linked")
        self.lbl_counts.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        row.addWidget(title)
        row.addWidget(self.lbl_summary, 1)
        row.addWidget(self.lbl_counts)
        log_l.addLayout(row)

        self.motion_log = QTextEdit()
        self.motion_log.setReadOnly(True)
        self.motion_log.setPlainText("...")
        self.motion_log.setStyleSheet(
            f"QTextEdit {{ background:{_PANEL_BG}; border:1px solid {C_BORDER}; color:{C_TEXT_DIM}; "
            f"font-family:'{Fonts.MONO}'; font-size:12px; }}"
        )
        log_l.addWidget(self.motion_log, 1)
        foot_l.addWidget(log_panel, 3)

        side_panel = _section()
        side_l = QVBoxLayout(side_panel)
        side_l.setContentsMargins(8, 6, 8, 6)
        side_l.setSpacing(5)
        side_title = QLabel("Sync Status")
        side_title.setStyleSheet(lbl(C_TEXT, mono=True, bold=True))
        _fix_h(side_title, 22)
        side_l.addWidget(side_title)
        self.lbl_sync_pico = QLabel("Picomotor      --")
        self.lbl_sync_kimm = QLabel("KIMM Z         --")
        self.lbl_sync_acs = QLabel("ACS Kinematic  --")
        for w in (self.lbl_sync_pico, self.lbl_sync_kimm, self.lbl_sync_acs):
            w.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            _fix_h(w, 22)
            side_l.addWidget(w)
        side_l.addStretch(1)
        foot_l.addWidget(side_panel, 1)

        lay.addWidget(foot)

    def _build_pico_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("motionCard")
        card.setStyleSheet(_card_frame())
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        conn_panel = _section_box("PICOMOTOR 8742", C_ACCENT)
        conn_l = conn_panel.content_layout()
        conn_l.setSpacing(6)
        conn_row = QHBoxLayout()
        self.btn_pico_connect = QPushButton("CONNECT")
        self.btn_pico_disconnect = QPushButton("DISCONNECT")
        self.btn_pico_connect.setStyleSheet(BTN_SMALL)
        self.btn_pico_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_pico_connect.clicked.connect(lambda: self._call_live_panel("motor_panel", "_on_connect"))
        self.btn_pico_disconnect.clicked.connect(lambda: self._call_live_panel("motor_panel", "_on_disconnect"))
        _fix_h(self.btn_pico_connect, 34)
        _fix_h(self.btn_pico_disconnect, 34)
        conn_row.addWidget(self.btn_pico_connect)
        conn_row.addWidget(self.btn_pico_disconnect)
        conn_l.addLayout(conn_row)
        status_row = QHBoxLayout()
        self.lbl_pico_model = QLabel("USB link idle")
        self.lbl_pico_model.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(self.lbl_pico_model, 28)
        self.lbl_pico_status = QLabel("● DISCONNECTED")
        self.lbl_pico_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pico_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        status_row.addWidget(self.lbl_pico_model, 1)
        status_row.addWidget(self.lbl_pico_status, 1)
        conn_l.addLayout(status_row)
        lay.addWidget(conn_panel)

        pos_panel = _section_box("MOTOR POSITIONS", C_DANGER)
        pos_l = pos_panel.content_layout()
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)
        self._pico_pos_labels = []
        for i in range(4):
            axis = QLabel(f"M{i+1}")
            axis.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            pos = QLabel("---")
            pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pos.setStyleSheet(lbl(C_TEXT, mono=True))
            _fix_h(axis, 24)
            _fix_h(pos, 24)
            grid.addWidget(axis, i, 0)
            grid.addWidget(pos, i, 1)
            self._pico_pos_labels.append(pos)
        pos_l.addLayout(grid)
        lay.addWidget(pos_panel)

        ctrl_panel = _section_box("AXIS CONTROLS", C_ACCENT)
        ctrl_l = ctrl_panel.content_layout()
        self._pico_cards = []
        card_grid = QGridLayout()
        card_grid.setSpacing(4)
        for idx in range(4):
            motor_card = MotorCard(idx + 1)
            motor_card.setMinimumHeight(130)
            motor_card.setMaximumHeight(148)
            motor_card.move_requested.connect(self._on_pico_move_requested)
            self._pico_cards.append(motor_card)
            card_grid.addWidget(motor_card, idx // 2, idx % 2)
        ctrl_l.addLayout(card_grid)
        lay.addWidget(ctrl_panel)

        btn_row = QHBoxLayout()
        self.btn_pico_zero_all = QPushButton("ZERO ALL")
        self.btn_pico_stop_all = QPushButton("STOP ALL")
        self.btn_pico_refresh = QPushButton("REFRESH")
        self.btn_pico_zero_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        self.btn_pico_stop_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_pico_refresh.setStyleSheet(BTN_SMALL)
        self.btn_pico_zero_all.clicked.connect(self._pico_zero_all)
        self.btn_pico_stop_all.clicked.connect(self._pico_stop_all)
        self.btn_pico_refresh.clicked.connect(self._refresh_from_sources)
        for btn in (self.btn_pico_zero_all, self.btn_pico_stop_all, self.btn_pico_refresh):
            _fix_h(btn, 30)
            btn_row.addWidget(btn)
        lay.addLayout(btn_row)
        lay.addStretch(1)

        return card

    def _build_kimm_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("motionCard")
        card.setStyleSheet(_card_frame())
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        conn_panel = _section_box("KIMM FINE STAGE (Z)", C_ACCENT)
        conn_l = conn_panel.content_layout()
        conn_l.setSpacing(6)
        row_ip = QHBoxLayout()
        lbl_ip = QLabel("IP")
        lbl_ip.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_ip.setFixedWidth(50)
        self.edit_kimm_ip = QLineEdit()
        self.edit_kimm_ip.setPlaceholderText("192.168.1.100")
        self.edit_kimm_ip.setStyleSheet(EDIT_STYLE)
        row_ip.addWidget(lbl_ip)
        row_ip.addWidget(self.edit_kimm_ip)
        conn_l.addLayout(row_ip)

        row_port = QHBoxLayout()
        lbl_port = QLabel("PORT")
        lbl_port.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_port.setFixedWidth(50)
        self.edit_kimm_port = QLineEdit()
        self.edit_kimm_port.setPlaceholderText("5000")
        self.edit_kimm_port.setFixedWidth(90)
        self.edit_kimm_port.setStyleSheet(EDIT_STYLE)
        row_port.addWidget(lbl_port)
        row_port.addWidget(self.edit_kimm_port)
        row_port.addStretch()
        conn_l.addLayout(row_port)

        btn_row = QHBoxLayout()
        self.btn_kimm_connect = QPushButton("CONNECT")
        self.btn_kimm_disconnect = QPushButton("DISCONNECT")
        self.btn_kimm_connect.setStyleSheet(BTN_SMALL)
        self.btn_kimm_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_kimm_connect.clicked.connect(self._connect_kimm_from_motion)
        self.btn_kimm_disconnect.clicked.connect(lambda: self._call_live_panel("kimm_z_panel", "_on_disconnect"))
        _fix_h(self.btn_kimm_connect, 34)
        _fix_h(self.btn_kimm_disconnect, 34)
        btn_row.addWidget(self.btn_kimm_connect)
        btn_row.addWidget(self.btn_kimm_disconnect)
        conn_l.addLayout(btn_row)
        conn_row = QHBoxLayout()
        self.lbl_kimm_link = QLabel("IP/Port --")
        self.lbl_kimm_link.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(self.lbl_kimm_link, 28)
        self.lbl_kimm_status = QLabel("● DISCONNECTED")
        self.lbl_kimm_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_kimm_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        conn_row.addWidget(self.lbl_kimm_link, 1)
        conn_row.addWidget(self.lbl_kimm_status, 1)
        conn_l.addLayout(conn_row)
        lay.addWidget(conn_panel)

        live_panel = _section_box("STATUS & POSITION", C_DANGER)
        live_l = live_panel.content_layout()
        live_l.setSpacing(6)
        z_panel = _section()
        z_l = QVBoxLayout(z_panel)
        z_l.setContentsMargins(8, 6, 8, 6)
        self.lbl_kimm_z = QLabel("--- um")
        self.lbl_kimm_z.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_kimm_z.setStyleSheet(
            f"color:#d8e8ff; font-family:'{Fonts.MONO}'; font-size:24px; font-weight:bold;"
        )
        z_l.addWidget(self.lbl_kimm_z)
        _fix_h(z_panel, 78)
        live_l.addWidget(z_panel)

        stat_panel = _section()
        stat_l = QHBoxLayout(stat_panel)
        stat_l.setContentsMargins(8, 4, 8, 4)
        stat_l.setSpacing(6)
        stat = QHBoxLayout()
        self.lbl_kimm_servo = QLabel("SERVO: OFF")
        self.lbl_kimm_limit = QLabel("LIMIT: --")
        self.lbl_kimm_vel = QLabel("VEL: --")
        self.lbl_kimm_dry = QLabel("DRY: OFF")
        for w in (self.lbl_kimm_servo, self.lbl_kimm_limit, self.lbl_kimm_vel, self.lbl_kimm_dry):
            w.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            _fix_h(w, 34)
            stat.addWidget(w)
        stat_l.addLayout(stat)
        _fix_h(stat_panel, 48)
        live_l.addWidget(stat_panel)
        lay.addWidget(live_panel)

        motion_panel = _section_box("MANUAL CONTROL", C_ACCENT)
        motion_l = motion_panel.content_layout()
        motion_l.setSpacing(6)
        jog = QGridLayout()
        jog.setSpacing(4)
        jog_specs = [
            ("+10", 10.0, 0, 0), ("+1", 1.0, 0, 1), ("+0.1", 0.1, 0, 2),
            ("-10", -10.0, 1, 0), ("-1", -1.0, 1, 1), ("-0.1", -0.1, 1, 2),
        ]
        self._kimm_jog_btns = []
        for text, delta, r, c in jog_specs:
            btn = QPushButton(text)
            btn.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_ACCENT if delta > 0 else C_DANGER))
            _fix_h(btn, 30)
            btn.clicked.connect(lambda _, d=delta: self._kimm_jog(d))
            jog.addWidget(btn, r, c)
            self._kimm_jog_btns.append(btn)
        motion_l.addLayout(jog)

        abs_row = QHBoxLayout()
        self.spin_kimm_abs = QDoubleSpinBox()
        self.spin_kimm_abs.setRange(-100000.0, 100000.0)
        self.spin_kimm_abs.setDecimals(3)
        self.spin_kimm_abs.setSuffix(" um")
        self.spin_kimm_abs.setStyleSheet(SPIN_STYLE)
        self.btn_kimm_go = QPushButton("GO")
        self.btn_kimm_go.setStyleSheet(BTN_SMALL)
        _fix_h(self.spin_kimm_abs, 30)
        _fix_h(self.btn_kimm_go, 30)
        self.btn_kimm_go.clicked.connect(self._kimm_abs_move)
        abs_row.addWidget(self.spin_kimm_abs, 1)
        abs_row.addWidget(self.btn_kimm_go)
        motion_l.addLayout(abs_row)
        lay.addWidget(motion_panel)

        return card

    def _build_acs_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("motionCard")
        card.setStyleSheet(_card_frame())
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card.setMinimumWidth(640)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        conn_panel = _section_box("CONNECTION", C_ACCENT)
        conn_l = conn_panel.content_layout()
        conn_l.setSpacing(6)
        row_ip = QHBoxLayout()
        lbl_ip = QLabel("IP")
        lbl_ip.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_ip.setFixedWidth(50)
        self.edit_acs_ip = QLineEdit()
        self.edit_acs_ip.setPlaceholderText("10.0.0.100")
        self.edit_acs_ip.setStyleSheet(EDIT_STYLE)
        row_ip.addWidget(lbl_ip)
        row_ip.addWidget(self.edit_acs_ip)
        conn_l.addLayout(row_ip)

        row_port = QHBoxLayout()
        lbl_port = QLabel("PORT")
        lbl_port.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        lbl_port.setFixedWidth(50)
        self.edit_acs_port = QLineEdit()
        self.edit_acs_port.setPlaceholderText("700")
        self.edit_acs_port.setFixedWidth(90)
        self.edit_acs_port.setStyleSheet(EDIT_STYLE)
        self.check_acs_sim = QCheckBox("SIM")
        self.check_acs_sim.setStyleSheet(CHECKBOX_STYLE)
        row_port.addWidget(lbl_port)
        row_port.addWidget(self.edit_acs_port)
        row_port.addStretch()
        row_port.addWidget(self.check_acs_sim)
        conn_l.addLayout(row_port)

        btn_row = QHBoxLayout()
        self.btn_acs_connect = QPushButton("CONNECT")
        self.btn_acs_disconnect = QPushButton("DISCONNECT")
        self.btn_acs_connect.setStyleSheet(BTN_SMALL)
        self.btn_acs_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_acs_connect.clicked.connect(self._connect_acs_from_motion)
        self.btn_acs_disconnect.clicked.connect(lambda: self._call_live_panel("acs_stage_panel", "_on_disconnect"))
        _fix_h(self.btn_acs_connect, 34)
        _fix_h(self.btn_acs_disconnect, 34)
        btn_row.addWidget(self.btn_acs_connect)
        btn_row.addWidget(self.btn_acs_disconnect)
        conn_l.addLayout(btn_row)
        conn_row = QHBoxLayout()
        self.lbl_acs_link = QLabel("Ethernet / Simulator")
        self.lbl_acs_link.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(self.lbl_acs_link, 28)
        self.lbl_acs_status = QLabel("● DISCONNECTED")
        self.lbl_acs_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_acs_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        conn_row.addWidget(self.lbl_acs_link, 1)
        conn_row.addWidget(self.lbl_acs_status, 1)
        conn_l.addLayout(conn_row)
        lay.addWidget(conn_panel)

        axis_panel = _section_box("6-AXIS POSITIONS", C_DANGER)
        axis_l = axis_panel.content_layout()
        axis_l.setSpacing(4)
        self._acs_axis_rows = []
        axis_grid = QGridLayout()
        axis_grid.setSpacing(3)
        axis_grid.setContentsMargins(0, 0, 0, 0)
        headers = ["Axis", "Pos", "State"]
        for col, txt in enumerate(headers):
            hdr = QLabel(txt)
            hdr.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            _fix_h(hdr, 20)
            axis_grid.addWidget(hdr, 0, col)
        for i, axis_name in enumerate(["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]):
            axis_lbl = QLabel(axis_name)
            axis_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            pos_lbl = QLabel("---")
            pos_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pos_lbl.setStyleSheet(lbl(C_TEXT, mono=True))
            state_lbl = QLabel("OFF")
            state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            state_lbl.setStyleSheet(_pill_style(C_TEXT_DEAD))
            _fix_h(axis_lbl, 24)
            _fix_h(pos_lbl, 24)
            _fix_h(state_lbl, 24)
            axis_grid.addWidget(axis_lbl, i + 1, 0)
            axis_grid.addWidget(pos_lbl, i + 1, 1)
            axis_grid.addWidget(state_lbl, i + 1, 2)
            self._acs_axis_rows.append((pos_lbl, state_lbl))
        axis_l.addLayout(axis_grid)
        lay.addWidget(axis_panel)

        global_panel = _section_box("GLOBAL CONTROL", C_ACCENT)
        global_l = global_panel.content_layout()
        global_row = QHBoxLayout()
        self.btn_acs_en_all = QPushButton("ENABLE ALL")
        self.btn_acs_dis_all = QPushButton("DISABLE ALL")
        self.btn_acs_stop_all = QPushButton("STOP ALL")
        self.btn_acs_en_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_ACCENT))
        self.btn_acs_dis_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        self.btn_acs_stop_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_acs_en_all.clicked.connect(self._acs_enable_all)
        self.btn_acs_dis_all.clicked.connect(self._acs_disable_all)
        self.btn_acs_stop_all.clicked.connect(self._acs_stop_all)
        for btn in (self.btn_acs_en_all, self.btn_acs_dis_all, self.btn_acs_stop_all):
            _fix_h(btn, 30)
            global_row.addWidget(btn)
        global_l.addLayout(global_row)
        lay.addWidget(global_panel)

        kin_panel = _section_box("6DOF KINEMATIC MOVE", "#aa7acc")
        kin_l = kin_panel.content_layout()
        self._dof_fields = {}
        dof_grid = QGridLayout()
        dof_grid.setSpacing(4)
        dof_grid.setContentsMargins(0, 0, 0, 0)
        headers = ["DOF", "ACTUAL (GET)", "TARGET (SET)", "STEP", "", ""]
        for col, text in enumerate(headers):
            h = QLabel(text)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            dof_grid.addWidget(h, 0, col)
        for i, name in enumerate(self._dof_order):
            lbl_name = QLabel(name)
            lbl_name.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            cur_lbl = QLabel("---")
            cur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cur_lbl.setStyleSheet(lbl(C_WARN, mono=True))
            spin = QDoubleSpinBox()
            spin.setRange(-500.0, 500.0)
            spin.setDecimals(4)
            spin.setStyleSheet(SPIN_STYLE)
            step_spin = QDoubleSpinBox()
            step_spin.setRange(0.0001, 100.0)
            step_spin.setDecimals(4)
            step_spin.setValue(0.1)
            step_spin.setStyleSheet(SPIN_STYLE)
            btn_m = QPushButton("−")
            btn_m.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
            btn_p = QPushButton("+")
            btn_p.setStyleSheet(BTN_SMALL)
            _fix_h(lbl_name, 26)
            _fix_h(cur_lbl, 26)
            _fix_h(spin, 26)
            _fix_h(step_spin, 26)
            _fix_h(btn_m, 26)
            _fix_h(btn_p, 26)
            self._dof_fields[name] = spin
            self._dof_actual_labels[name] = cur_lbl
            self._dof_step_spins[name] = step_spin
            btn_m.clicked.connect(lambda _, n=name: self._on_dof_jog(n, -1))
            btn_p.clicked.connect(lambda _, n=name: self._on_dof_jog(n, +1))
            dof_grid.addWidget(lbl_name, i + 1, 0)
            dof_grid.addWidget(cur_lbl, i + 1, 1)
            dof_grid.addWidget(spin, i + 1, 2)
            dof_grid.addWidget(step_spin, i + 1, 3)
            dof_grid.addWidget(btn_m, i + 1, 4)
            dof_grid.addWidget(btn_p, i + 1, 5)
        kin_l.addLayout(dof_grid)

        self.btn_sync_get_to_set = QPushButton("SYNC ACTUAL (GET) → TARGET (SET)")
        self.btn_sync_get_to_set.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        _fix_h(self.btn_sync_get_to_set, 28)
        self.btn_sync_get_to_set.clicked.connect(self._acs_sync_actual)
        kin_l.addWidget(self.btn_sync_get_to_set)

        btn_row = QHBoxLayout()
        self.btn_acs_sync = QPushButton("SYNC ACTUAL")
        self.btn_acs_calc = QPushButton("CALC KINEMATICS")
        self.btn_acs_exec = QPushButton("EXECUTE MOVE")
        self.btn_acs_stop = QPushButton("STOP")
        self.btn_acs_sync.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        self.btn_acs_calc.setStyleSheet(BTN_SMALL.replace(C_ACCENT, "#aa7acc"))
        self.btn_acs_exec.setStyleSheet(BTN_SMALL)
        self.btn_acs_stop.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_acs_sync.clicked.connect(self._acs_sync_actual)
        self.btn_acs_calc.clicked.connect(self._acs_calc_only)
        self.btn_acs_exec.clicked.connect(self._acs_execute_move)
        self.btn_acs_stop.clicked.connect(self._acs_stop_all)
        for btn in (self.btn_acs_sync, self.btn_acs_calc, self.btn_acs_exec, self.btn_acs_stop):
            _fix_h(btn, 30)
            btn_row.addWidget(btn)
        kin_l.addLayout(btn_row)

        self.kin_result = QTextEdit()
        self.kin_result.setReadOnly(True)
        self.kin_result.setFixedHeight(120)
        self.kin_result.setStyleSheet(
            f"QTextEdit {{ background:{_PANEL_BG}; border:1px solid {C_BORDER}; color:#c0d0ff; "
            f"font-family:'{Fonts.MONO}'; font-size:12px; }}"
        )
        kin_l.addWidget(self.kin_result)

        row_set = QHBoxLayout()
        self.check_dry = QCheckBox("DRY RUN")
        self.check_dry.setStyleSheet(
            f"QCheckBox {{ color:{C_WARN}; font-family:'{Fonts.UI}'; font-size:13px; font-weight:bold; }}"
        )
        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 10000)
        self.spin_settle.setValue(500)
        self.spin_settle.setFixedWidth(90)
        self.spin_settle.setStyleSheet(SPIN_STYLE)
        lbl_settle = QLabel("Settle(ms):")
        lbl_settle.setStyleSheet(lbl(C_TEXT_DIM))
        row_set.addWidget(self.check_dry)
        row_set.addStretch(1)
        row_set.addWidget(lbl_settle)
        row_set.addWidget(self.spin_settle)
        kin_l.addLayout(row_set)

        lay.addWidget(kin_panel)

        # Separate section: kinematic settings and default physical parameters
        kin_set_panel = _section_box("KINEMATIC SETTINGS", C_WARN)
        set_l = kin_set_panel.content_layout()
        set_l.setSpacing(5)

        defaults_head = QLabel("PHYSICAL PARAMETER EDITOR")
        defaults_head.setStyleSheet(lbl(C_WARN, mono=True, bold=True))
        set_l.addWidget(defaults_head)

        setup_grid = QGridLayout()
        setup_grid.setSpacing(4)
        setup_grid.addWidget(QLabel("Stage Setup"), 0, 0)
        for c, text in enumerate(["X", "Y", "Z"]):
            h = QLabel(text)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            setup_grid.addWidget(h, 0, c + 1)
        setup_vals = list(self._calc.stage_setup.reshape(-1))
        idx = 0
        for r in range(3):
            s_lbl = QLabel(f"S{r+1}")
            s_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            setup_grid.addWidget(s_lbl, r + 1, 0)
            for c in range(3):
                spin = QDoubleSpinBox()
                spin.setRange(-99999.0, 99999.0)
                spin.setDecimals(4)
                spin.setValue(float(setup_vals[idx]))
                spin.setStyleSheet(SPIN_STYLE)
                _fix_h(spin, 24)
                self._stage_setup_spins.append(spin)
                setup_grid.addWidget(spin, r + 1, c + 1)
                idx += 1
        set_l.addLayout(setup_grid)

        enc_grid = QGridLayout()
        enc_grid.setSpacing(4)
        enc_grid.addWidget(QLabel("Encoder Pos"), 0, 0)
        for c, text in enumerate(["X", "Y", "Z"]):
            h = QLabel(text)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            enc_grid.addWidget(h, 0, c + 1)
        enc_vals = list(self._calc.encoder_pos.reshape(-1))
        idx = 0
        for r in range(3):
            s_lbl = QLabel(f"S{r+1}")
            s_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            enc_grid.addWidget(s_lbl, r + 1, 0)
            for c in range(3):
                spin = QDoubleSpinBox()
                spin.setRange(-99999.0, 99999.0)
                spin.setDecimals(4)
                spin.setValue(float(enc_vals[idx]))
                spin.setStyleSheet(SPIN_STYLE)
                _fix_h(spin, 24)
                self._encoder_pos_spins.append(spin)
                enc_grid.addWidget(spin, r + 1, c + 1)
                idx += 1
        set_l.addLayout(enc_grid)

        lim_grid = QGridLayout()
        lim_grid.setSpacing(4)
        for c, h in enumerate(["Axis", "+Limit", "-Limit"]):
            hh = QLabel(h)
            hh.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hh.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            lim_grid.addWidget(hh, 0, c)
        for i, axis in enumerate(self._motor_axis_names):
            r = i + 1
            ax = QLabel(axis)
            ax.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            sp = QDoubleSpinBox()
            sm = QDoubleSpinBox()
            for s in (sp, sm):
                s.setRange(-99999.0, 99999.0)
                s.setDecimals(4)
                s.setStyleSheet(SPIN_STYLE)
                _fix_h(s, 24)
            sp.setValue(float(self._calc.plus_limits[i]))
            sm.setValue(float(self._calc.minus_limits[i]))
            self._limit_plus_spins.append(sp)
            self._limit_minus_spins.append(sm)
            lim_grid.addWidget(ax, r, 0)
            lim_grid.addWidget(sp, r, 1)
            lim_grid.addWidget(sm, r, 2)
        set_l.addLayout(lim_grid)

        dir_grid = QGridLayout()
        dir_grid.setSpacing(3)
        dir_grid.addWidget(QLabel("Direction"), 0, 0)
        for c, t in enumerate(["X", "Y", "Z"]):
            h = QLabel(t)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            dir_grid.addWidget(h, 0, c + 1)
        direction = list(self._calc.direction.reshape(-1))
        idx = 0
        for r in range(3):
            l = QLabel(f"S{r+1}")
            l.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            dir_grid.addWidget(l, r + 1, 0)
            for c in range(3):
                cb = QComboBox()
                cb.addItems(["+1", "-1"])
                cb.setCurrentIndex(0 if float(direction[idx]) >= 0 else 1)
                cb.setStyleSheet(EDIT_STYLE)
                _fix_h(cb, 24)
                self._dir_combos.append(cb)
                dir_grid.addWidget(cb, r + 1, c + 1)
                idx += 1
        set_l.addLayout(dir_grid)

        map_grid = QGridLayout()
        map_grid.setSpacing(3)
        map_grid.addWidget(QLabel("Map"), 0, 0)
        for c, t in enumerate(["X", "Y", "Z"]):
            h = QLabel(t)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            map_grid.addWidget(h, 0, c + 1)
        mapping = list(self._calc._mapping.reshape(-1))
        idx = 0
        for r in range(3):
            l = QLabel(f"S{r+1}")
            l.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            map_grid.addWidget(l, r + 1, 0)
            for c in range(3):
                chk = QCheckBox("Slave")
                chk.setChecked(float(mapping[idx]) >= 0.5)
                chk.setToolTip("Checked = Slave(1), unchecked = Master(0)")
                chk.setStyleSheet(CHECKBOX_STYLE)
                _fix_h(chk, 24)
                self._map_combos.append(chk)
                map_grid.addWidget(chk, r + 1, c + 1)
                idx += 1
        set_l.addLayout(map_grid)

        row_apply = QHBoxLayout()
        self.btn_kin_apply = QPushButton("APPLY SETTINGS")
        self.btn_kin_save = QPushButton("SAVE")
        self.btn_kin_load = QPushButton("LOAD")
        self.btn_kin_reset = QPushButton("RESET DEFAULT")
        self.btn_kin_apply.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        self.btn_kin_save.setStyleSheet(BTN_SMALL)
        self.btn_kin_load.setStyleSheet(BTN_SMALL)
        self.btn_kin_reset.setStyleSheet(BTN_SMALL)
        _fix_h(self.btn_kin_apply, 28)
        _fix_h(self.btn_kin_save, 28)
        _fix_h(self.btn_kin_load, 28)
        _fix_h(self.btn_kin_reset, 28)
        self.btn_kin_apply.clicked.connect(self._apply_kinematic_settings_from_ui)
        self.btn_kin_save.clicked.connect(self._save_kinematic_settings_ui)
        self.btn_kin_load.clicked.connect(self._load_kinematic_settings_ui)
        self.btn_kin_reset.clicked.connect(self._reset_kinematic_settings_ui)
        row_apply.addWidget(self.btn_kin_apply)
        row_apply.addWidget(self.btn_kin_save)
        row_apply.addWidget(self.btn_kin_load)
        row_apply.addWidget(self.btn_kin_reset)
        set_l.addLayout(row_apply)
        lay.addWidget(kin_set_panel)
        self._load_kinematic_settings_ui(apply_after_load=False)

        hint = QLabel("Tx/Ty/Tz/Rx/Ry/Rz -> CalPos -> Y1/Z1/X1/Z2/Y2/Z3")
        hint.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(hint, 30)
        lay.addWidget(hint)
        lay.addStretch(1)

        return card

    # ------------------------------------------------------------------
    # Source helpers
    # ------------------------------------------------------------------

    def _pico_ctrl(self):
        if not self._live_tab:
            return None
        panel = getattr(self._live_tab, "motor_panel", None)
        return getattr(panel, "controller", None) if panel else None

    def _kimm_ctrl(self):
        if not self._live_tab:
            return None
        panel = getattr(self._live_tab, "kimm_z_panel", None)
        return getattr(panel, "controller", None) if panel else None

    def _acs_ctrl(self):
        if not self._live_tab:
            return None
        panel = getattr(self._live_tab, "acs_stage_panel", None)
        return getattr(panel, "controller", None) if panel else None

    def _call_live_panel(self, panel_name: str, method_name: str):
        if not self._live_tab:
            return
        panel = getattr(self._live_tab, panel_name, None)
        fn = getattr(panel, method_name, None) if panel else None
        if callable(fn):
            try:
                fn()
            except Exception as e:
                self.log_message.emit(f"{panel_name}.{method_name} failed: {e}")
        self._refresh_from_sources()

    def _connect_kimm_from_motion(self):
        panel = getattr(self._live_tab, "kimm_z_panel", None) if self._live_tab else None
        if panel:
            panel.edit_ip.setText(self.edit_kimm_ip.text().strip())
            panel.edit_port.setText(self.edit_kimm_port.text().strip())
        self._call_live_panel("kimm_z_panel", "_on_connect")

    def _connect_acs_from_motion(self):
        panel = getattr(self._live_tab, "acs_stage_panel", None) if self._live_tab else None
        if panel:
            panel.edit_ip.setText(self.edit_acs_ip.text().strip())
            panel.edit_port.setText(self.edit_acs_port.text().strip())
            if hasattr(panel, "check_sim"):
                panel.check_sim.setChecked(self.check_acs_sim.isChecked())
        self._call_live_panel("acs_stage_panel", "_on_connect")

    def _sync_connection_fields_from_live(self):
        if not self._live_tab:
            return
        kimm_panel = getattr(self._live_tab, "kimm_z_panel", None)
        if kimm_panel:
            if not self.edit_kimm_ip.text().strip():
                self.edit_kimm_ip.setText(kimm_panel.edit_ip.text())
            if not self.edit_kimm_port.text().strip():
                self.edit_kimm_port.setText(kimm_panel.edit_port.text())
        acs_panel = getattr(self._live_tab, "acs_stage_panel", None)
        if acs_panel:
            if not self.edit_acs_ip.text().strip():
                self.edit_acs_ip.setText(acs_panel.edit_ip.text())
            if not self.edit_acs_port.text().strip():
                self.edit_acs_port.setText(acs_panel.edit_port.text())
            if hasattr(acs_panel, "check_sim"):
                self.check_acs_sim.setChecked(acs_panel.check_sim.isChecked())

    def _run_bg(self, label: str, fn):
        def worker():
            try:
                fn()
            except Exception as e:
                self.log_message.emit(f"{label} failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Refresh / sync
    # ------------------------------------------------------------------

    def _refresh_from_sources(self):
        pico = self._pico_ctrl()
        kimm = self._kimm_ctrl()
        acs = self._acs_ctrl()

        pico_ok = bool(pico and pico.is_connected)
        kimm_ok = bool(kimm and kimm.is_connected)
        acs_ok = bool(acs and acs.is_connected)

        # Picomotor
        if pico_ok:
            try:
                self._pico_positions = pico.get_all_positions()
                self.lbl_pico_status.setText("● CONNECTED")
                self.lbl_pico_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
                self.lbl_pico_model.setText(f"Model: {getattr(pico, '_cmdlib', None) and getattr(pico, '_cmdlib', None).__class__.__name__ or 'USB'}")
            except Exception as e:
                self.lbl_pico_model.setText(f"Read error: {e}")
        else:
            self._pico_positions = [None, None, None, None]
            self.lbl_pico_status.setText("● DISCONNECTED")
            self.lbl_pico_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
            self.lbl_pico_model.setText("USB link idle")

        # Global Simulation Override
        if self.check_global_sim.isChecked():
            from core.v2.drivers.simulators import AcsSimulatorV2, KimmSimulatorV2, PicoSimulatorV2
            if not isinstance(self._hub.pico._hal, PicoSimulatorV2):
                self._hub.pico.set_hal(PicoSimulatorV2())
                self.log_message.emit("HUB: Pico Simulator Injected")
            if not isinstance(self._hub.kimm._hal, KimmSimulatorV2):
                self._hub.kimm.set_hal(KimmSimulatorV2())
                self.log_message.emit("HUB: KIMM Simulator Injected")
            if not isinstance(self._hub.acs._hal, AcsSimulatorV2):
                self._hub.acs.set_hal(AcsSimulatorV2())
                self.log_message.emit("HUB: ACS Simulator Injected")
            
            # Treat as "Connected" for UI state
            pico_ok = kimm_ok = acs_ok = True
        else:
            # Update V2 Adapters for Hub (Inject only once or when connection changes)
            if pico_ok and not self._hub.pico.has_hal:
                self._hub.pico.set_hal(PicoAdapterV2(pico))
                self.log_message.emit("PICO V2 Engine Active")
            elif not pico_ok and self._hub.pico.has_hal:
                self._hub.pico.set_hal(None)

            if kimm_ok and not self._hub.kimm.has_hal:
                self._hub.kimm.set_hal(KimmAdapterV2(kimm))
                self.log_message.emit("KIMM V2 Engine Active")
            elif not kimm_ok and self._hub.kimm.has_hal:
                self._hub.kimm.set_hal(None)

            if acs_ok and not self._hub.acs.has_hal:
                self._hub.acs.set_hal(AcsAdapterV2(acs))
                self.log_message.emit("ACS V2 Engine Active")
            elif not acs_ok and self._hub.acs.has_hal:
                self._hub.acs.set_hal(None)

        for i, lbl_pos in enumerate(self._pico_pos_labels):
            val = self._pico_positions[i] if i < len(self._pico_positions) else None
            lbl_pos.setText(f"{val:,}" if val is not None else "---")
        for card in self._pico_cards:
            card.set_enabled(pico_ok)
        self.btn_pico_connect.setEnabled(not pico_ok)
        self.btn_pico_disconnect.setEnabled(pico_ok)
        self.lbl_hdr_pico_link.setText("Picomotor: LIVE" if pico_ok else "Picomotor: --")
        self.lbl_hdr_pico_link.setStyleSheet(_pill_style(C_ACCENT if pico_ok else C_TEXT_DIM))
        self.lbl_strip_pico.value_label.setText("USB open / 4 CH" if pico_ok else "USB idle / 4 CH")
        self.lbl_sync_pico.setText(f"Picomotor      {'LIVE' if pico_ok else '--'}")

        # KIMM
        if self.check_global_sim.isChecked():
            # In sim mode, read from Hub simulator
            sim = self._hub.kimm._hal
            self._kimm_z = sim.get_positions()[2] if sim else 0.0
            self.lbl_kimm_status.setText("● SIMULATING")
            self.lbl_kimm_status.setStyleSheet(lbl(C_WARN, mono=True, bold=True))
            self.lbl_kimm_link.setText("V2 Simulator")
        elif kimm_ok:
            self._kimm_z = kimm.current_z
            self.lbl_kimm_status.setText("● CONNECTED")
            self.lbl_kimm_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.lbl_kimm_link.setText(f"{kimm.ip}:{kimm.port}")
        else:
            self._kimm_z = None
            self.lbl_kimm_status.setText("● DISCONNECTED")
            self.lbl_kimm_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
            self.lbl_kimm_link.setText("IP/Port --")
        
        self.lbl_kimm_z.setText(f"{self._kimm_z:+.3f} um" if self._kimm_z is not None else "--- um")
        
        # Status flags for KIMM (Hybrid support)
        k_servo = (kimm_ok and kimm.servo_on) or self.check_global_sim.isChecked()
        k_dry = (kimm_ok and kimm.dry_run) or False
        
        self.lbl_kimm_servo.setText(f"SERVO: {'ON' if k_servo else 'OFF'}")
        self.lbl_kimm_servo.setStyleSheet(lbl(C_ACCENT if k_servo else C_TEXT_DEAD, mono=True, bold=True))
        self.lbl_kimm_limit.setText(f"LIMIT: {getattr(kimm, 'z_safety_limit', '--')}")
        self.lbl_kimm_vel.setText(f"VEL: {getattr(kimm, 'default_velocity', '--')}")
        self.lbl_kimm_dry.setText(f"DRY: {'ON' if k_dry else 'OFF'}")
        for btn in self._kimm_jog_btns:
            btn.setEnabled(kimm_ok)
        self.btn_kimm_go.setEnabled(kimm_ok)
        self.btn_kimm_connect.setEnabled(not kimm_ok)
        self.btn_kimm_disconnect.setEnabled(kimm_ok)
        self.edit_kimm_ip.setEnabled(not kimm_ok)
        self.edit_kimm_port.setEnabled(not kimm_ok)
        self.lbl_hdr_kimm_link.setText("KIMM Z: LIVE" if kimm_ok else "KIMM Z: --")
        self.lbl_hdr_kimm_link.setStyleSheet(_pill_style(C_ACCENT if kimm_ok else C_TEXT_DIM))
        self.lbl_strip_kimm.value_label.setText("TCP connected / Z Axis" if kimm_ok else "TCP idle / Z Axis")
        self.lbl_sync_kimm.setText(f"KIMM Z         {'LIVE' if kimm_ok else '--'}")

        # ACS
        if self.check_global_sim.isChecked():
            sim = self._hub.acs._hal
            self.lbl_acs_status.setText("● SIMULATING")
            self.lbl_acs_status.setStyleSheet(lbl(C_WARN, mono=True, bold=True))
            self.lbl_acs_link.setText("V2 Simulator")
            self._acs_positions = sim.get_positions() if sim else [0.0]*6
            self._acs_states = [
                {
                    "enabled": sim.is_enabled(i) if sim else False,
                    "moving": sim.is_moving(i) if sim else False,
                    "in_pos": True
                }
                for i in range(6)
            ]
        elif acs_ok:
            self.lbl_acs_status.setText("● CONNECTED")
            self.lbl_acs_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.lbl_acs_link.setText("Simulator" if acs.is_simulator else "Ethernet TCP")
            self._acs_positions = [acs.get_position(i) for i in range(6)]
            self._acs_states = [
                {
                    "enabled": acs.is_enabled(i),
                    "moving": acs.is_moving(i),
                    "in_pos": not acs.is_moving(i) and acs.is_enabled(i),
                }
                for i in range(6)
            ]
        else:
            self.lbl_acs_status.setText("● DISCONNECTED")
            self.lbl_acs_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
            self.lbl_acs_link.setText("Ethernet / Simulator")
            self._acs_positions = [0.0] * 6
            self._acs_states = [{"enabled": False, "moving": False, "in_pos": False} for _ in range(6)]

        for i, name in enumerate(self._dof_order):
            if name in self._dof_actual_labels:
                text = f"{self._acs_positions[i]:+.4f}" if acs_ok else "---"
                self._dof_actual_labels[name].setText(text)

        for i, (pos_lbl, state_lbl) in enumerate(self._acs_axis_rows):
            pos_lbl.setText(f"{self._acs_positions[i]:+.4f} mm" if acs_ok else "---")
            st = self._acs_states[i]
            if st["moving"]:
                state_lbl.setText("MOVING")
                state_lbl.setStyleSheet(_pill_style(C_WARN))
            elif st["enabled"]:
                state_lbl.setText("ENABLED")
                state_lbl.setStyleSheet(_pill_style(C_ACCENT))
            else:
                state_lbl.setText("OFF")
                state_lbl.setStyleSheet(_pill_style(C_TEXT_DEAD))

        for w in (self.btn_acs_sync, self.btn_acs_calc, self.btn_acs_exec, self.btn_acs_stop,
                  self.btn_acs_en_all, self.btn_acs_dis_all, self.btn_acs_stop_all, self.btn_sync_get_to_set):
            w.setEnabled(acs_ok or (w is self.btn_acs_calc and kinematic_available()))
        self.btn_acs_connect.setEnabled(not acs_ok)
        self.btn_acs_disconnect.setEnabled(acs_ok)
        self.edit_acs_ip.setEnabled(not acs_ok)
        self.edit_acs_port.setEnabled(not acs_ok)
        self.check_acs_sim.setEnabled(not acs_ok)
        for name in self._dof_order:
            self._dof_fields[name].setEnabled(acs_ok)
            self._dof_step_spins[name].setEnabled(acs_ok)
        self.check_dry.setEnabled(True)
        self.spin_settle.setEnabled(True)
        self.lbl_hdr_acs_link.setText("ACS: SIM" if (acs_ok and acs.is_simulator) else ("ACS: LIVE" if acs_ok else "ACS: --"))
        self.lbl_hdr_acs_link.setStyleSheet(_pill_style(C_ACCENT if acs_ok else C_TEXT_DIM))
        self.lbl_strip_acs.value_label.setText(
            "Simulator / kinematic" if (acs_ok and acs.is_simulator)
            else ("Ethernet / kinematic" if acs_ok else "Ethernet or SIM / 6 Axis")
        )
        self.lbl_sync_acs.setText(f"ACS Kinematic  {'SIM' if (acs_ok and acs.is_simulator) else ('LIVE' if acs_ok else '--')}")

        self._update_summary()

    def _record_log_message(self, msg: str):
        self._log_lines.append(msg)
        self._log_lines = self._log_lines[-8:]
        if hasattr(self, "motion_log"):
            self.motion_log.setPlainText("\n".join(self._log_lines) if self._log_lines else "...")

    def _update_summary(self):
        linked = sum(
            bool(x)
            for x in (self._pico_ctrl() and self._pico_ctrl().is_connected,
                      self._kimm_ctrl() and self._kimm_ctrl().is_connected,
                      self._acs_ctrl() and self._acs_ctrl().is_connected)
        )
        motion = sum(1 for st in self._acs_states if st.get("moving"))
        self.lbl_counts.setText(f"{linked}/3 linked")
        if motion:
            self.lbl_summary.setText(f"{motion} ACS axis moving")
            self.lbl_summary.setStyleSheet(lbl(C_WARN, mono=True, bold=True))
        elif linked == 3:
            self.lbl_summary.setText("All motion links healthy")
            self.lbl_summary.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.lbl_summary.setText("Partial link state")
            self.lbl_summary.setStyleSheet(lbl(C_TEXT, mono=True, bold=True))

    # ------------------------------------------------------------------
    # V2 Hub Callbacks
    # ------------------------------------------------------------------

    def _on_hub_busy_changed(self, busy: bool):
        """Global UI Locking based on Hub state."""
        # Disable all major movement buttons when any device is busy
        btns = [
            self.btn_pico_zero_all, self.btn_pico_stop_all,
            self.btn_kimm_go, self.btn_acs_exec, self.btn_acs_sync,
            self.btn_acs_en_all, self.btn_acs_dis_all,
        ]
        # Include jog buttons
        btns.extend(self._kimm_jog_btns)
        
        for btn in btns:
            btn.setEnabled(not busy)
            
        if busy:
            self.lbl_summary.setText("MOTION IN PROGRESS...")
            self.lbl_summary.setStyleSheet(lbl(C_WARN, mono=True, bold=True))

    def _on_hub_state_summary(self, summary: str):
        """Update a dedicated hub status label or footer."""
        # We can put this in the sync status section or footer log
        self.lbl_summary.setText(summary)

    def _on_global_sim_toggled(self, checked: bool):
        self._hub.stop_all_immediate()
        self._hub.pico.set_hal(None)
        self._hub.kimm.set_hal(None)
        self._hub.acs.set_hal(None)
        self._refresh_from_sources()
        self.log_message.emit(f"Global Simulation: {'ENABLED' if checked else 'DISABLED'}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_pico_move_requested(self, motor_num: int, steps: int):
        if steps == 0:
            self._hub.move_pico(motor_num - 1, 0)
            self.log_message.emit(f"PICO M{motor_num} Zero requested")
        else:
            self._hub.move_pico(motor_num - 1, steps)
            self.log_message.emit(f"PICO M{motor_num} Move: {steps} steps")

    def _pico_zero_all(self):
        self.log_message.emit("PICO Zero All requested (Sequential V2)")
        for i in range(4):
            self._hub.move_pico(i, 0)
        pico = self._pico_ctrl()
        if not pico or not pico.is_connected:
            return

        def run():
            try:
                for m in range(1, 5):
                    pico.zero(m)
                self.log_message.emit("Picomotor: ZERO ALL")
            except Exception as e:
                self.log_message.emit(f"Picomotor ZERO ALL failed: {e}")

        self._run_bg("Picomotor ZERO ALL", run)

    def _pico_stop_all(self):
        pico = self._pico_ctrl()
        if not pico or not pico.is_connected:
            return
        self._run_bg("Picomotor STOP ALL", lambda: pico.stop_all())
        self.log_message.emit("Picomotor: STOP ALL")

    def _kimm_abs_move(self):
        target = self.spin_kimm_abs.value()
        res = self._hub.move_kimm_z(target, settling_ms=200)
        self.log_message.emit(f"KIMM Move to {target}um: {res.message}")

    def _kimm_jog(self, delta: float):
        # Hybrid Hub doesn't have a relative jog yet, but we can compute it
        if self._kimm_z is not None:
            target = self._kimm_z + delta
            self._hub.move_kimm_z(target, settling_ms=50)

    def _acs_sync_actual(self):
        acs = self._acs_ctrl()
        if not acs or not acs.is_connected:
            self.log_message.emit("ACS is not connected.")
            return
        for i, name in enumerate(["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]):
            self._dof_fields[name].setValue(acs.get_position(i))
        self._acs_calc_only()
        self.log_message.emit("ACS: synced actual pose into target fields")

    def _acs_calc_only(self):
        self._apply_kinematic_settings_from_ui()
        values = {name: spin.value() for name, spin in self._dof_fields.items()}
        cal_pos, ball_pos, ok, violations = self._calc.calculate(
            [values["Tx"], values["Ty"], values["Tz"]],
            [values["Rx"], values["Ry"], values["Rz"]],
        )

        self._last_cal_pos = cal_pos
        if cal_pos is None:
            self.kin_result.setPlainText("Kinematic calculation unavailable.")
            return

        lines = [
            f"Tx/Ty/Tz: {values['Tx']:+.4f}, {values['Ty']:+.4f}, {values['Tz']:+.4f}",
            f"Rx/Ry/Rz: {values['Rx']:+.4f}, {values['Ry']:+.4f}, {values['Rz']:+.4f}",
            "",
            "CalPos:",
        ]
        axes = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
        for axis, val in zip(axes, cal_pos):
            lines.append(f"  {axis:<3} {val:+.4f} mm")
        if violations:
            lines.append("")
            lines.append("Limit notes:")
            lines.extend(f"  - {v}" for v in violations[:8])
        if ball_pos is not None:
            lines.append("")
            lines.append(f"Ball pos: {ball_pos}")
        self.kin_result.setPlainText("\n".join(lines))

    def _acs_execute_move(self):
        if self._last_cal_pos is None:
            self._acs_calc_only()
        if self._last_cal_pos is None:
            return

        cal_pos = list(self._last_cal_pos)
        settle = self.spin_settle.value()
        
        res = self._hub.move_acs_6dof(cal_pos, settling_ms=settle)
        self.log_message.emit(f"ACS Kinematic Move: {res.message}")

    def _acs_enable_all(self):
        acs = self._acs_ctrl()
        if acs and acs.is_connected:
            self._run_bg("ACS enable all", acs.enable_all)

    def _acs_disable_all(self):
        acs = self._acs_ctrl()
        if acs and acs.is_connected:
            self._run_bg("ACS disable all", acs.disable_all)

    def _acs_stop_all(self):
        acs = self._acs_ctrl()
        if acs and acs.is_connected:
            self._run_bg("ACS stop all", acs.stop_all)

    def _all_stop(self):
        self._hub.stop_all_immediate()
        self.log_message.emit("EMERGENCY GLOBAL STOP REQUESTED")

    def _reconnect_all(self):
        if not self._live_tab:
            return
        # Reuse the live tab's stored connection UI/state when available.
        actions = []
        if hasattr(self._live_tab, "motor_panel") and not self._pico_ctrl():
            actions.append(getattr(self._live_tab.motor_panel, "_on_connect", None))
        if hasattr(self._live_tab, "kimm_z_panel") and not self._kimm_ctrl():
            actions.append(getattr(self._live_tab.kimm_z_panel, "_on_connect", None))
        if hasattr(self._live_tab, "acs_stage_panel") and not self._acs_ctrl():
            actions.append(getattr(self._live_tab.acs_stage_panel, "_on_connect", None))

        ran = False
        for fn in actions:
            if callable(fn):
                try:
                    fn()
                    ran = True
                except Exception as e:
                    self.log_message.emit(f"Reconnect failed: {e}")
        if ran:
            self.log_message.emit("MotionTab: reconnect requested from live settings")
        self._refresh_from_sources()

    def _on_dof_jog(self, dof_name: str, sign: int):
        if dof_name not in self._dof_fields:
            return
        step = self._dof_step_spins[dof_name].value()
        spin = self._dof_fields[dof_name]
        spin.setValue(spin.value() + (step * float(sign)))
        self._acs_calc_only()

    def _apply_kinematic_settings_from_ui(self):
        for i, spin in enumerate(self._stage_setup_spins):
            self._calc.stage_setup.reshape(-1)[i] = float(spin.value())
        for i, spin in enumerate(self._encoder_pos_spins):
            self._calc.encoder_pos.reshape(-1)[i] = float(spin.value())

        # Axis limits
        for i in range(6):
            self._calc.plus_limits[i] = float(self._limit_plus_spins[i].value())
            self._calc.minus_limits[i] = float(self._limit_minus_spins[i].value())

        # Direction sign (+1 / -1) as Stage x XYZ matrix.
        dflat = self._calc.direction.reshape(-1).copy()
        for i, cb in enumerate(self._dir_combos):
            dflat[i] = 1.0 if cb.currentIndex() == 0 else -1.0
        self._calc.direction = dflat.reshape(self._calc.direction.shape)

        # Master/Slave mapping (0/1 for sm1X..sm3Z)
        m = []
        for chk in self._map_combos:
            m.append(1.0 if chk.isChecked() else 0.0)
        self._calc._mapping[:] = m
        self.log_message.emit("Kinematic settings applied.")

    def _reset_kinematic_settings_ui(self):
        for i, spin in enumerate(self._stage_setup_spins):
            spin.setValue(float(self._default_stage_setup.reshape(-1)[i]))
        for i, spin in enumerate(self._encoder_pos_spins):
            spin.setValue(float(self._default_encoder_pos.reshape(-1)[i]))
        for i in range(6):
            self._limit_plus_spins[i].setValue(float(self._default_plus_limits[i]))
            self._limit_minus_spins[i].setValue(float(self._default_minus_limits[i]))
        dflat = list(self._default_direction.reshape(-1))
        for i, cb in enumerate(self._dir_combos):
            cb.setCurrentIndex(0 if float(dflat[i]) >= 0 else 1)
        default_map = list(self._default_mapping.reshape(-1))
        for i, chk in enumerate(self._map_combos):
            chk.setChecked(default_map[i] >= 0.5)
        self._apply_kinematic_settings_from_ui()

    def _save_kinematic_settings_ui(self):
        self._apply_kinematic_settings_from_ui()
        s = self._settings
        prefix = "motion/kinematic"
        s.setValue(f"{prefix}/stage_setup", [sp.value() for sp in self._stage_setup_spins])
        s.setValue(f"{prefix}/encoder_pos", [sp.value() for sp in self._encoder_pos_spins])
        s.setValue(f"{prefix}/plus_limits", [sp.value() for sp in self._limit_plus_spins])
        s.setValue(f"{prefix}/minus_limits", [sp.value() for sp in self._limit_minus_spins])
        s.setValue(f"{prefix}/direction", [1 if cb.currentIndex() == 0 else -1 for cb in self._dir_combos])
        s.setValue(f"{prefix}/mapping", [1 if chk.isChecked() else 0 for chk in self._map_combos])
        self.log_message.emit("Kinematic settings saved.")

    def _load_kinematic_settings_ui(self, apply_after_load: bool = True):
        s = self._settings
        prefix = "motion/kinematic"

        def _vals(key: str, default: list[float]) -> list[float]:
            raw = s.value(f"{prefix}/{key}", default)
            if isinstance(raw, str):
                raw = [raw]
            try:
                return [float(v) for v in raw]
            except Exception:
                return default

        stage = _vals("stage_setup", [float(v) for v in self._default_stage_setup.reshape(-1)])
        enc = _vals("encoder_pos", [float(v) for v in self._default_encoder_pos.reshape(-1)])
        plus = _vals("plus_limits", [float(v) for v in self._default_plus_limits])
        minus = _vals("minus_limits", [float(v) for v in self._default_minus_limits])
        direction = _vals("direction", [float(v) for v in self._default_direction.reshape(-1)])
        mapping = _vals("mapping", [float(v) for v in self._default_mapping.reshape(-1)])

        for spins, vals in ((self._stage_setup_spins, stage), (self._encoder_pos_spins, enc)):
            for spin, val in zip(spins, vals):
                spin.setValue(float(val))
        for spin, val in zip(self._limit_plus_spins, plus):
            spin.setValue(float(val))
        for spin, val in zip(self._limit_minus_spins, minus):
            spin.setValue(float(val))
        for cb, val in zip(self._dir_combos, direction):
            cb.setCurrentIndex(0 if float(val) >= 0 else 1)
        for chk, val in zip(self._map_combos, mapping):
            chk.setChecked(float(val) >= 0.5)

        if apply_after_load:
            self._apply_kinematic_settings_from_ui()
            self.log_message.emit("Kinematic settings loaded.")
