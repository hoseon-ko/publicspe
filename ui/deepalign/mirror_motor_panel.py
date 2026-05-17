"""
ui/deepalign/mirror_motor_panel.py
DeepAlign 🪞 탭 — Picomotor 4축 제어 패널.

MotionTab pico_card 스타일 UI.
두 가지 연결 방식 지원:
  1. 자체 연결: CONNECT 버튼 → PicomotorController.connect() + start_polling()
  2. 주입 연결: set_controller(ctrl) → LiveTab 컨트롤러 공유
     이 경우 update_positions()로 위치를 수신한다.
"""

from __future__ import annotations
from PyQt6.QtWidgets import QSpinBox

from typing import Optional, List

from PyQt6.QtCore import Qt, pyqtSignal, QTimer  # QTimer: MotorCard.flash_moving에서 사용
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QSizePolicy,
)

from core.motor.picomotor import PicomotorController
from core.logger import dev_logger
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import C_ACCENT, C_DANGER, C_WARN, C_TEXT, C_TEXT_DIM, Fonts, BTN_SMALL, lbl


# ── MotorCard 전용 스타일 ───────────────────────────────────────────────────
_CARD_STYLE = """
    QFrame#motorCard {
        background: #0f1729;
        border: 1px solid #0f3460;
        border-radius: 6px;
    }
"""
_SPIN_STYLE = """
    QSpinBox, QDoubleSpinBox {
        background: #080e1e; border: 1px solid #0f3460;
        color: #c0d0ff; border-radius: 3px;
        font-family: 'Courier New'; font-size: 14px; padding: 2px 4px;
    }
"""


class MotorCard(QFrame):
    """1개 축의 포지션 표시 + 스텝 이동 컨트롤."""

    move_requested = pyqtSignal(int, int)   # (motor_num, steps); steps==0 → ZERO

    def __init__(self, motor_num: int, parent=None):
        super().__init__(parent)
        self.motor_num = motor_num
        self.setObjectName("motorCard")
        self.setStyleSheet(_CARD_STYLE)
        self._build_ui()
        self.set_enabled(False)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # 헤더: 번호 + 상태 점
        top = QHBoxLayout()
        lbl_num = QLabel(f"M{self.motor_num}")
        lbl_num.setStyleSheet(
            "color: #4ecdc4; font-family: 'Courier New'; font-weight: bold; font-size: 15px;"
        )
        self.lbl_dot = QLabel("●")
        self.lbl_dot.setStyleSheet("color: #2a3a6a; font-family: 'Courier New'; font-size: 13px;")
        top.addWidget(lbl_num)
        top.addStretch()
        top.addWidget(self.lbl_dot)
        layout.addLayout(top)

        # 포지션
        self.lbl_pos = QLabel("—")
        self.lbl_pos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pos.setStyleSheet("""
            color: #e0e8ff; font-family: 'Courier New'; font-size: 21px; font-weight: bold;
            background: #080e1e; border: 1px solid #0f3460; border-radius: 4px; padding: 3px;
        """)
        layout.addWidget(self.lbl_pos)

        # 스텝 입력
        step_row = QHBoxLayout()
        lbl_s = QLabel("Steps")
        lbl_s.setStyleSheet("color: #3a5080; font-size: 13px; font-family: 'Courier New';")
        self.spin = QSpinBox()
        self.spin.setRange(-999999, 999999)
        self.spin.setValue(100)
        self.spin.setFixedWidth(85)
        self.spin.setStyleSheet(_SPIN_STYLE)
        step_row.addWidget(lbl_s)
        step_row.addWidget(self.spin)
        layout.addLayout(step_row)

        # 빠른 스텝 선택
        quick = QHBoxLayout()
        quick.setSpacing(3)
        for v in [10, 50, 100, 500]:
            b = QPushButton(str(v))
            b.setFixedHeight(20)
            b.setStyleSheet("""
                QPushButton {
                    background: #111a30; color: #3a5080;
                    border: 1px solid #0f3460; border-radius: 3px;
                    font-size: 13px; font-family: 'Courier New'; padding: 0;
                }
                QPushButton:hover { color: #4ecdc4; border-color: #4ecdc4; }
            """)
            b.clicked.connect(lambda _, val=v: self.spin.setValue(val))
            quick.addWidget(b)
        layout.addLayout(quick)

        # 이동 버튼
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_neg = QPushButton("◀ −")
        self.btn_neg.setStyleSheet("""
            QPushButton { background: #0d1e38; color: #4ecdc4; border: 1px solid #1a4060;
                border-radius: 4px; font-family: 'Courier New'; font-weight: bold; }
            QPushButton:hover { background: #1a3a60; }
            QPushButton:disabled { color: #1a2840; background: #080e1e; border-color: #0a1828; }
        """)
        self.btn_pos = QPushButton("+ ▶")
        self.btn_pos.setStyleSheet("""
            QPushButton { background: #0d2820; color: #4ecdc4; border: 1px solid #1a5040;
                border-radius: 4px; font-family: 'Courier New'; font-weight: bold; }
            QPushButton:hover { background: #1a4838; }
            QPushButton:disabled { color: #1a2840; background: #080e1e; border-color: #0a1828; }
        """)
        self.btn_zero = QPushButton("ZERO")
        self.btn_zero.setStyleSheet("""
            QPushButton { background: #281020; color: #e94560; border: 1px solid #5a2040;
                border-radius: 4px; font-family: 'Courier New'; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: #3a1830; }
            QPushButton:disabled { color: #1a2840; background: #080e1e; border-color: #0a1828; }
        """)
        btn_row.addWidget(self.btn_neg)
        btn_row.addWidget(self.btn_pos)
        btn_row.addWidget(self.btn_zero)
        layout.addLayout(btn_row)

        self.btn_neg.clicked.connect(lambda: self.move_requested.emit(self.motor_num, -self.spin.value()))
        self.btn_pos.clicked.connect(lambda: self.move_requested.emit(self.motor_num, self.spin.value()))
        self.btn_zero.clicked.connect(lambda: self.move_requested.emit(self.motor_num, 0))

    def set_position(self, pos: Optional[int]):
        if pos is None:
            self.lbl_pos.setText("ERR")
            self.lbl_dot.setText("●")
            self.lbl_dot.setStyleSheet("color: #e94560; font-family: 'Courier New'; font-size: 13px;")
        else:
            self.lbl_pos.setText(f"{pos:,}")
            self.lbl_dot.setText("●")   # flash_moving 후 텍스트 복원
            self.lbl_dot.setStyleSheet("color: #4ecdc4; font-family: 'Courier New'; font-size: 13px;")

    def flash_moving(self, steps: int):
        """이동 명령 시 상태 인디케이터를 노란색으로 잠깐 점등."""
        dir_str = "→" if steps > 0 else ("←" if steps < 0 else "⊙")
        self.lbl_dot.setText(f"{dir_str}")
        self.lbl_dot.setStyleSheet("color: #ffe66d; font-family: 'Courier New'; font-size: 13px;")
        # 1.5초 후 복원 (폴링으로 위치 갱신되면 자동으로 teal 복구)
        QTimer.singleShot(1500, self._reset_dot)

    def _reset_dot(self):
        self.lbl_dot.setText("●")
        self.lbl_dot.setStyleSheet("color: #4ecdc4; font-family: 'Courier New'; font-size: 13px;")

    def set_enabled(self, en: bool):
        for w in (self.btn_neg, self.btn_pos, self.btn_zero, self.spin):
            w.setEnabled(en)
        if not en:
            self.lbl_pos.setText("—")
            self.lbl_dot.setText("●")
            self.lbl_dot.setStyleSheet("color: #2a3a6a; font-family: 'Courier New'; font-size: 13px;")


# ─── 로컬 스타일 헬퍼 ──────────────────────────────────────────────────────────

def _section_box(title: str, accent: str = C_ACCENT) -> CollapsibleSection:
    sec = CollapsibleSection(title, accent=accent)
    sec.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return sec


def _fix_h(widget: QWidget, h: int) -> None:
    widget.setMinimumHeight(h)
    widget.setMaximumHeight(h)


# ─────────────────────────────────────────────────────────────────────────────


class MirrorMotorPanel(QWidget):
    """
    DeepAlign 🪞 탭 Picomotor 4축 제어 패널.

    Signals
    -------
    connected(str)       – 연결 완료 (모델명)
    disconnected()       – 연결 해제
    positions_updated(list) – 위치 갱신 [p1, p2, p3, p4]
    log_message(str)     – 로그 메시지
    """

    connected         = pyqtSignal(str)
    disconnected      = pyqtSignal()
    positions_updated = pyqtSignal(list)
    log_message       = pyqtSignal(str)
    # SCAN — list[(motor_1based, target_steps_abs)], settle_ms, avg_frames
    scan_requested    = pyqtSignal(list, int, int)
    scan_stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctrl: Optional[PicomotorController] = None
        self._is_own_ctrl: bool = False          # True = 직접 연결, False = 주입
        self._session_hub = None
        self._pico_cards: List[MotorCard] = []
        self._pico_pos_labels: List[QLabel] = []
        self._build_ui()
        self._apply_connected(False)

    # ── UI 빌드 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── 1. CONNECTION ─────────────────────────────────────────────────────
        conn_sec = _section_box("PICOMOTOR 8742", C_ACCENT)
        cl = conn_sec.content_layout()
        cl.setSpacing(5)
        cl.setContentsMargins(6, 6, 6, 6)

        btn_row = QHBoxLayout()
        self.btn_connect    = QPushButton("CONNECT")
        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_connect.setStyleSheet(BTN_SMALL)
        self.btn_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        _fix_h(self.btn_connect,    30)
        _fix_h(self.btn_disconnect, 30)
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        btn_row.addWidget(self.btn_connect)
        btn_row.addWidget(self.btn_disconnect)
        cl.addLayout(btn_row)

        status_row = QHBoxLayout()
        self.lbl_model = QLabel("USB link idle")
        self.lbl_model.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(self.lbl_model, 24)
        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        _fix_h(self.lbl_status, 24)
        status_row.addWidget(self.lbl_model, 1)
        status_row.addWidget(self.lbl_status, 1)
        cl.addLayout(status_row)
        root.addWidget(conn_sec)

        # ── 2. MOTOR POSITIONS ────────────────────────────────────────────────
        pos_sec = _section_box("MOTOR POSITIONS", C_DANGER)
        pl = pos_sec.content_layout()
        pl.setContentsMargins(6, 4, 6, 4)
        pl.setSpacing(2)

        pos_grid = QGridLayout()
        pos_grid.setSpacing(3)
        pos_grid.setContentsMargins(0, 0, 0, 0)
        self._pico_pos_labels = []
        for i in range(4):
            ax = QLabel(f"M{i + 1}")
            ax.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            pv = QLabel("---")
            pv.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pv.setStyleSheet(lbl(C_TEXT, mono=True))
            _fix_h(ax, 22)
            _fix_h(pv, 22)
            pos_grid.addWidget(ax, i, 0)
            pos_grid.addWidget(pv, i, 1)
            self._pico_pos_labels.append(pv)
        pl.addLayout(pos_grid)
        root.addWidget(pos_sec)

        # ── 3. AXIS CONTROLS ──────────────────────────────────────────────────
        ctrl_sec = _section_box("AXIS CONTROLS", C_ACCENT)
        ctl = ctrl_sec.content_layout()
        ctl.setContentsMargins(4, 4, 4, 4)
        ctl.setSpacing(4)

        card_grid = QGridLayout()
        card_grid.setSpacing(4)
        self._pico_cards = []
        for idx in range(4):
            card = MotorCard(idx + 1)
            card.setMinimumHeight(130)
            card.setMaximumHeight(150)
            card.set_enabled(False)
            card.move_requested.connect(self._on_move_requested)
            self._pico_cards.append(card)
            card_grid.addWidget(card, idx // 2, idx % 2)
        ctl.addLayout(card_grid)
        root.addWidget(ctrl_sec)

        # ── 4. 버튼 행 ───────────────────────────────────────────────────────
        btn_row2 = QHBoxLayout()
        self.btn_zero_all = QPushButton("ZERO ALL")
        self.btn_stop_all = QPushButton("STOP ALL")
        self.btn_refresh  = QPushButton("REFRESH")
        self.btn_zero_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_WARN))
        self.btn_stop_all.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_refresh.setStyleSheet(BTN_SMALL)
        self.btn_zero_all.clicked.connect(self._on_zero_all)
        self.btn_stop_all.clicked.connect(self._on_stop_all)
        self.btn_refresh.clicked.connect(self._refresh_positions)
        for b in (self.btn_zero_all, self.btn_stop_all, self.btn_refresh):
            _fix_h(b, 30)
            btn_row2.addWidget(b)
        root.addLayout(btn_row2)

        # ── 5. SCAN ──────────────────────────────────────────────────────────
        self._build_scan_section(root)

        root.addStretch(1)

    # ── SCAN UI ───────────────────────────────────────────────────────────────

    def _build_scan_section(self, root: QVBoxLayout) -> None:
        scan_sec = _section_box("SCAN (M1 sweep)", C_ACCENT)
        sl = scan_sec.content_layout()
        sl.setSpacing(4)
        sl.setContentsMargins(6, 6, 6, 6)

        # 파라미터: motor / N steps / step delta / settle / avg
        grid = QGridLayout()
        grid.setSpacing(4)

        def _lbl(t: str) -> QLabel:
            l = QLabel(t)
            l.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            return l

        def _spin(lo: int, hi: int, val: int) -> QSpinBox:
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setStyleSheet(_SPIN_STYLE)
            return s

        self.scan_spin_motor   = _spin(1, 4, 1)
        self.scan_spin_n       = _spin(2, 999, 5)
        self.scan_spin_delta   = _spin(-100000, 100000, 100)
        self.scan_spin_settle  = _spin(0, 10000, 200)
        self.scan_spin_avg     = _spin(1, 32, 1)

        grid.addWidget(_lbl("Motor"),     0, 0); grid.addWidget(self.scan_spin_motor,  0, 1)
        grid.addWidget(_lbl("N points"),  0, 2); grid.addWidget(self.scan_spin_n,      0, 3)
        grid.addWidget(_lbl("Δ steps"),   1, 0); grid.addWidget(self.scan_spin_delta,  1, 1)
        grid.addWidget(_lbl("Settle ms"), 1, 2); grid.addWidget(self.scan_spin_settle, 1, 3)
        grid.addWidget(_lbl("Avg frames"),2, 0); grid.addWidget(self.scan_spin_avg,    2, 1)
        sl.addLayout(grid)

        # Start/Stop + 상태
        btn_row = QHBoxLayout()
        self.btn_scan_start = QPushButton("SCAN START")
        self.btn_scan_stop  = QPushButton("SCAN STOP")
        self.btn_scan_start.setStyleSheet(BTN_SMALL)
        self.btn_scan_stop.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        _fix_h(self.btn_scan_start, 30)
        _fix_h(self.btn_scan_stop,  30)
        self.btn_scan_stop.setEnabled(False)
        self.btn_scan_start.clicked.connect(self._on_scan_start)
        self.btn_scan_stop.clicked.connect(self._on_scan_stop)
        btn_row.addWidget(self.btn_scan_start)
        btn_row.addWidget(self.btn_scan_stop)
        sl.addLayout(btn_row)

        self.lbl_scan_status = QLabel("idle")
        self.lbl_scan_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_scan_status.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
        _fix_h(self.lbl_scan_status, 22)
        sl.addWidget(self.lbl_scan_status)

        root.addWidget(scan_sec)

    def _on_scan_start(self) -> None:
        motor = int(self.scan_spin_motor.value())
        n     = int(self.scan_spin_n.value())
        delta = int(self.scan_spin_delta.value())

        cur = self._ctrl.get_position(motor) if self._ctrl is not None else None
        if cur is None:
            self.lbl_scan_status.setText("❌ 위치 조회 실패 (연결?)")
            self.lbl_scan_status.setStyleSheet(lbl(C_DANGER, mono=True))
            return

        points = [(motor, int(cur) + delta * i) for i in range(n)]
        self.scan_requested.emit(
            points,
            int(self.scan_spin_settle.value()),
            int(self.scan_spin_avg.value()),
        )

    def _on_scan_stop(self) -> None:
        self.scan_stop_requested.emit()

    def set_scan_status(self, msg: str, kind: str = "info") -> None:
        """외부(main_tab)에서 스캔 상태 갱신용."""
        color_map = {"info": C_TEXT_DIM, "ok": C_ACCENT, "warn": C_WARN, "err": C_DANGER}
        self.lbl_scan_status.setText(msg)
        self.lbl_scan_status.setStyleSheet(lbl(color_map.get(kind, C_TEXT_DIM), mono=True))

    def set_scan_running(self, running: bool) -> None:
        self.btn_scan_start.setEnabled(not running)
        self.btn_scan_stop.setEnabled(running)

    # ── 연결 상태 UI 적용 ─────────────────────────────────────────────────────

    def _apply_connected(self, ok: bool, model: str = "") -> None:
        self.btn_connect.setEnabled(not ok)
        self.btn_disconnect.setEnabled(ok)
        self.btn_zero_all.setEnabled(ok)
        self.btn_stop_all.setEnabled(ok)
        self.btn_refresh.setEnabled(ok)
        for card in self._pico_cards:
            card.set_enabled(ok)
        if ok:
            self.lbl_status.setText(f"● {model or 'CONNECTED'}")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.lbl_model.setText(model or "USB Connected")
        else:
            self.lbl_status.setText("● DISCONNECTED")
            self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
            self.lbl_model.setText("USB link idle")
            for lbl_p in self._pico_pos_labels:
                lbl_p.setText("---")
            for card in self._pico_cards:
                card.set_position(None)

    # ── 직접 연결 ─────────────────────────────────────────────────────────────

    def _on_connect(self) -> None:
        if self._session_hub is not None:
            try:
                self._session_hub.connect_pico()
                self._session_hub.start_pico_polling()
                self._apply_connected(True, "Picomotor 8742")
                self.connected.emit("Picomotor 8742")
                self.log_message.emit("✅ Picomotor 연결 (hub)")
            except Exception as e:
                self.log_message.emit(f"❌ 연결 오류: {e}")
                dev_logger.exception("[MirrorMotorPanel] connect via hub failed")
            return
        try:
            self._ctrl = PicomotorController()
            model = self._ctrl.connect()
            self._is_own_ctrl = True
            self._ctrl.start_polling(self.update_positions, self._on_connection_lost)
            self._apply_connected(True, model)
            self.connected.emit(model)
            self.log_message.emit(f"✅ Picomotor 연결: {model}")
        except Exception as e:
            self._ctrl = None
            self._is_own_ctrl = False
            self.log_message.emit(f"❌ 연결 오류: {e}")
            dev_logger.exception("[MirrorMotorPanel] connect failed")

    def _on_disconnect(self) -> None:
        if self._session_hub is not None:
            try:
                self._session_hub.disconnect_pico()  # stop_pico_polling()도 내부에서 호출
            except Exception:
                pass
            self._apply_connected(False)
            self.disconnected.emit()
            self.log_message.emit("Picomotor 연결 해제 (hub)")
            return
        if self._is_own_ctrl and self._ctrl:
            try:
                self._ctrl.stop_polling()
                self._ctrl.disconnect()
            except Exception:
                pass
        self._ctrl = None
        self._is_own_ctrl = False
        self._apply_connected(False)
        self.disconnected.emit()
        self.log_message.emit("Picomotor 연결 해제")

    def _on_connection_lost(self) -> None:
        dev_logger.warning("[MirrorMotorPanel] Connection lost")
        self._ctrl = None
        self._is_own_ctrl = False
        self._apply_connected(False)
        self.disconnected.emit()
        self.log_message.emit("⚠️ Picomotor 연결 끊김")

    # ── SessionHub 연동 ───────────────────────────────────────────────────────

    def bind_session_hub(self, hub) -> None:
        """DeepAlignMainTab.bind_session_hub()에서 호출 — hub 경유 모드 활성화."""
        if self._session_hub:
            try:
                self._session_hub.event_published.disconnect(self._on_session_event)
            except Exception:
                pass
            try:
                self._session_hub.pico_positions_updated.disconnect(self.update_positions)
            except Exception:
                pass
        self._session_hub = hub
        if hub:
            hub.event_published.connect(self._on_session_event)
            # Hub-side 폴링 시그널 직접 구독 — UI QTimer 불필요
            hub.pico_positions_updated.connect(self.update_positions)

    def _on_session_event(self, event) -> None:
        from core.session.session_events import SessionEventType
        if event.event_type == SessionEventType.PICO_CONNECTED:
            self._session_hub.start_pico_polling()
            self._apply_connected(True, "Picomotor 8742")
        elif event.event_type == SessionEventType.PICO_DISCONNECTED:
            # stop_pico_polling은 disconnect_pico에서 자동 호출됨
            self._apply_connected(False)

    # ── 위치 갱신 ─────────────────────────────────────────────────────────────

    def update_positions(self, positions: list) -> None:
        """LiveTab motor_panel.positions_updated 또는 자체 폴링으로부터 위치 수신."""
        for i, lbl_p in enumerate(self._pico_pos_labels):
            val = positions[i] if i < len(positions) else None
            lbl_p.setText(f"{val:,}" if val is not None else "---")
        for i, card in enumerate(self._pico_cards):
            card.set_position(positions[i] if i < len(positions) else None)
        self.positions_updated.emit(list(positions))

    def _refresh_positions(self) -> None:
        """REFRESH 버튼 — 즉시 위치 조회."""
        if self._session_hub is not None:
            try:
                positions = [self._session_hub.pico_get_position(ax) for ax in range(1, 5)]
                self.update_positions(positions)
            except Exception as e:
                self.log_message.emit(f"위치 조회 오류: {e}")
            return
        if self._ctrl and self._ctrl.is_connected:
            try:
                self.update_positions(self._ctrl.get_all_positions())
            except Exception as e:
                self.log_message.emit(f"위치 조회 오류: {e}")

    # ── 모터 이동 ─────────────────────────────────────────────────────────────

    def _on_move_requested(self, motor_num: int, steps: int) -> None:
        if self._session_hub is not None:
            self._pico_cards[motor_num - 1].flash_moving(steps)
            try:
                if steps == 0:
                    self._session_hub.pico_zero(motor_num)
                    self.log_message.emit(f"Motor {motor_num}: ZERO via hub")
                else:
                    self._session_hub.pico_move_relative(motor_num, steps)
                    sign = "+" if steps > 0 else ""
                    self.log_message.emit(f"Motor {motor_num}: {sign}{steps} steps → OK")
            except Exception as e:
                self.log_message.emit(f"Motor {motor_num} 오류: {e}")
                dev_logger.exception("[MirrorMotorPanel] move via hub error")
            return
        if self._ctrl is None or not self._ctrl.is_connected:
            return
        self._pico_cards[motor_num - 1].flash_moving(steps)
        try:
            if steps == 0:
                self._ctrl.zero(motor_num)
                self.log_message.emit(f"Motor {motor_num}: ZERO set")
            else:
                ok   = self._ctrl.move_relative(motor_num, steps)
                sign = "+" if steps > 0 else ""
                self.log_message.emit(
                    f"Motor {motor_num}: {sign}{steps} steps → {'OK' if ok else 'FAIL'}"
                )
        except Exception as e:
            self.log_message.emit(f"Motor {motor_num} 오류: {e}")
            dev_logger.exception("[MirrorMotorPanel] move error")

    def _on_zero_all(self) -> None:
        if self._session_hub is not None:
            errors = []
            for i in range(1, 5):
                try:
                    self._session_hub.pico_zero(i)
                except Exception as e:
                    errors.append(f"M{i}:{e}")
            if errors:
                self.log_message.emit(f"⚠️ ZERO ALL 일부 실패: {', '.join(errors)}")
            else:
                self.log_message.emit("⊙ 전체 포지션 카운터 초기화 완료 (hub)")
            return
        if self._ctrl is None or not self._ctrl.is_connected:
            return
        errors = []
        for i in range(1, 5):
            try:
                self._ctrl.zero(i)
            except Exception as e:
                errors.append(f"M{i}:{e}")
        if errors:
            self.log_message.emit(f"⚠️ ZERO ALL 일부 실패: {', '.join(errors)}")
        else:
            self.log_message.emit("⊙ 전체 포지션 카운터 초기화 완료")

    def _on_stop_all(self) -> None:
        if self._session_hub is not None:
            try:
                self._session_hub.pico_stop_all()
                self.log_message.emit("⛔ 전체 모터 정지 (hub)")
            except Exception as e:
                self.log_message.emit(f"정지 오류: {e}")
            return
        if self._ctrl is None:
            return
        try:
            self._ctrl.stop_all()
            self.log_message.emit("⛔ 전체 모터 정지")
        except Exception as e:
            self.log_message.emit(f"정지 오류: {e}")

    # ── 공개 API (MotorPanel 인터페이스 호환) ─────────────────────────────────

    def set_controller(self, ctrl: Optional[PicomotorController]) -> None:
        """LiveTab에서 컨트롤러 주입. ctrl=None 이면 주입 컨트롤러만 해제한다.
        자체 연결(_is_own_ctrl)이 있는 경우 ctrl=None 호출을 무시한다.
        """
        if ctrl is None:
            if self._is_own_ctrl:
                return          # 자체 연결이면 LiveTab disconnect에 영향받지 않음
            self._ctrl = None
            self._apply_connected(False)
            return

        # 기존 자체 연결이 있었다면 먼저 종료
        if self._is_own_ctrl and self._ctrl:
            try:
                self._ctrl.stop_polling()
                self._ctrl.disconnect()
            except Exception:
                pass

        self._ctrl = ctrl
        self._is_own_ctrl = False
        if ctrl.is_connected:
            model = getattr(ctrl, "model_name", "") or "Connected"
            self._apply_connected(True, model)
        dev_logger.info("[MirrorMotorPanel] controller injected via set_controller()")

    def zero_all(self) -> None:
        """Master Bar ZERO ALL 버튼."""
        self._on_zero_all()

    def stop_all(self) -> None:
        """Master Bar STOP 버튼."""
        self._on_stop_all()

    def reset_controller(self) -> None:
        """Master Bar RESET 버튼 — 모션 중단(best-effort)."""
        if self._session_hub is not None:
            try:
                self._session_hub.pico_stop_all()
            except Exception:
                pass
            return
        if self._ctrl:
            try:
                self._ctrl.stop_all()
            except Exception:
                pass

    def move(self, motor_num: int, steps: int) -> bool:
        """외부(ScanWorker 등)에서 모터 이동."""
        if self._session_hub is not None:
            try:
                if steps == 0:
                    self._session_hub.pico_zero(motor_num)
                else:
                    self._session_hub.pico_move_relative(motor_num, steps)
                return True
            except Exception as e:
                self.log_message.emit(f"Motor {motor_num} 이동 오류: {e}")
                return False
        if self._ctrl is None or not self._ctrl.is_connected:
            return False
        try:
            if steps == 0:
                self._ctrl.zero(motor_num)
                return True
            return bool(self._ctrl.move_relative(motor_num, steps))
        except Exception as e:
            self.log_message.emit(f"Motor {motor_num} 이동 오류: {e}")
            return False

    def get_positions(self) -> list:
        if self._session_hub is not None:
            try:
                return [self._session_hub.pico_get_position(ax) for ax in range(1, 5)]
            except Exception:
                return [None, None, None, None]
        if self._ctrl and self._ctrl.is_connected:
            try:
                return self._ctrl.get_all_positions()
            except Exception:
                pass
        return [None, None, None, None]

    def stop_polling(self) -> None:
        """앱 종료 시 자체 연결 종료. Hub 폴링은 Hub가 관리, 주입 컨트롤러는 LiveTab이 관리."""
        if self._is_own_ctrl and self._ctrl:
            try:
                self._ctrl.stop_polling()
                self._ctrl.disconnect()
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        if self._session_hub is not None:
            return self._session_hub.is_pico_connected()
        return self._ctrl is not None and self._ctrl.is_connected

    @property
    def controller(self) -> Optional[PicomotorController]:
        return self._ctrl
