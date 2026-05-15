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
from ui.widgets.pico_card import PicoCard
from ui.widgets.kimm_z_card import KimmZCard
from ui.widgets.acs_card import AcsCard
from ui.widgets.acs_settings_panel import AcsSettingsCard
from ui.widgets.collapsible_section import CollapsibleSection
from core.v2.motion.engine import MotionState, MotionResult


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
        self._session_hub = None
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


        self._build_ui()
        self._refresh_timer.start()

    # ------------------------------------------------------------------
    # Binding
    # ------------------------------------------------------------------

    def bind_live_tab(self, live_tab):
        self._live_tab = live_tab
        self._sync_connection_fields_from_live()
        self._refresh_from_sources()

    def _on_session_event(self, event):
        from core.session.session_events import SessionEventType
        if event.event_type in (
            SessionEventType.PICO_CONNECTED,  SessionEventType.PICO_DISCONNECTED,
            SessionEventType.KIMM_CONNECTED,  SessionEventType.KIMM_DISCONNECTED,
            SessionEventType.ACS_CONNECTED,   SessionEventType.ACS_DISCONNECTED,
        ):
            self._refresh_from_sources()

    def cleanup(self):
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()

    def _refresh_from_sources(self) -> None:
        # Each card has its own internal timer and polling logic when bound to session hub.
        # Here we just ensure any tab-level summary is updated if needed.
        self._update_summary()

    def _get_pico_status(self):
        if not self._session_hub: return False, [None]*4
        return self._session_hub.is_pico_connected(), self._session_hub.pico_get_all_positions()

    def _get_kimm_status(self):
        if not self._session_hub: return False, False, None
        return self._session_hub.is_kimm_connected(), False, self._session_hub.kimm_get_z()

    def _get_acs_status(self):
        if not self._session_hub: return False, [0.0]*6
        return self._session_hub.is_acs_connected(), self._session_hub.acs_get_positions()

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
        self.acs_settings_card = AcsSettingsCard(self._calc)
        
        self._motion_cards = [
            self.pico_card, self.kimm_card, 
            self.acs_card, self.acs_settings_card
        ]
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

    def _build_pico_card(self) -> QWidget:
        self.pico_card = PicoCard()
        return self.pico_card

    def _build_kimm_card(self) -> QWidget:
        self.kimm_card = KimmZCard()
        return self.kimm_card

    def _build_acs_card(self) -> QWidget:
        self.acs_card = AcsCard()
        return self.acs_card

    def bind_session_hub(self, hub: "DeviceSessionHub"):
        self._session_hub = hub
        
        self.kimm_card.bind_session_hub(hub)
        self.acs_card.bind_session_hub(hub)
        self.pico_card.bind_session_hub(hub)
        self.acs_settings_card.bind_session_hub(hub)

        self._refresh_timer.start()

    def _refresh_from_sources(self):
        if not self._session_hub: return
        try:
            # 1. KIMM
            kimm_ok = self._session_hub.is_kimm_connected()
            z = self._session_hub.kimm_get_z()
            self.kimm_card.update_status(kimm_ok, z, False)
            
            # 2. ACS
            acs_ok = self._session_hub.is_acs_connected()
            if hasattr(self._session_hub, "acs_get_positions"):
                pos = self._session_hub.acs_get_positions()
                states = self._session_hub.acs_get_axis_states() if acs_ok else None
                self.acs_card.update_status(acs_ok, pos, states)
            
            # 3. Picomotor
            self.pico_card.refresh_status()

            # 4. Global Summary
            self._update_summary()

        except Exception as e:
            # dev_logger.error(f"MotionTab Refresh Error: {e}")
            pass

    def _update_summary(self):
        if not self._session_hub:
            self.lbl_counts.setText("0/3 linked")
            self.lbl_summary.setText("Session Hub not bound")
            return

        p_ok = self._session_hub.is_pico_connected()
        k_ok = self._session_hub.is_kimm_connected()
        a_ok = self._session_hub.is_acs_connected()
        
        linked = sum([p_ok, k_ok, a_ok])
        self.lbl_counts.setText(f"{linked}/3 linked")
        
        moving = False
        if a_ok:
            try:
                states = self._session_hub.acs_get_axis_states()
                moving = any(s.get("moving", False) for s in states)
            except: pass

        if moving:
            self.lbl_summary.setText("MOTION IN PROGRESS...")
            self.lbl_summary.setStyleSheet(lbl(C_WARN, mono=True, bold=True))
        elif linked == 3:
            self.lbl_summary.setText("All motion links healthy")
            self.lbl_summary.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
        else:
            self.lbl_summary.setText("Partial link state")
            self.lbl_summary.setStyleSheet(lbl(C_TEXT, mono=True, bold=True))

    def _on_hub_busy_changed(self, busy: bool):
        if busy:
            self.lbl_summary.setText("MOTION IN PROGRESS...")
            self.lbl_summary.setStyleSheet(lbl(C_WARN, mono=True, bold=True))

    def _on_hub_state_summary(self, summary: str):
        self.lbl_summary.setText(summary)

    def _record_log_message(self, msg: str):
        self._log_lines.append(msg)
        self._log_lines = self._log_lines[-8:]
        if hasattr(self, "motion_log"):
            self.motion_log.setPlainText("\n".join(self._log_lines) if self._log_lines else "...")

    def _all_stop(self):
        if self._session_hub:
            self._session_hub.acs_stop_all()
            self._session_hub.pico_stop_all()
            self._session_hub.kimm_stop()
            self.log_message.emit("EMERGENCY GLOBAL STOP (SessionHub Path)")
        else:
            self.log_message.emit("EMERGENCY GLOBAL STOP (SessionHub not bound)")

    def _reconnect_all(self):
        if self._session_hub:
            self._session_hub.reconnect_all()
        else:
            self.log_message.emit("MotionTab: Reconnect failed (Session Hub not bound)")

    def _on_global_sim_toggled(self, checked: bool):
        if self._session_hub:
            # SessionHub level sim toggle handling would go here
            pass
        self._refresh_from_sources()
        self.log_message.emit(f"Global Simulation: {'ENABLED' if checked else 'DISABLED'}")

    # ── DeepAlign Master Bar Aliases ──────────────────────────────────
    def refresh_positions(self):
        self._refresh_from_sources()

    def reconnect_all_devices(self):
        self._reconnect_all()

    def stop_all_motion(self):
        self._all_stop()
