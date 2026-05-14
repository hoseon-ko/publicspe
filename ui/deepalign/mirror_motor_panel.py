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

from typing import Optional, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QSizePolicy,
)

from core.motor.picomotor import PicomotorController
from core.logger import dev_logger
from ui.live.motor_panel import MotorCard
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import C_ACCENT, C_DANGER, C_WARN, C_TEXT, C_TEXT_DIM, Fonts, BTN_SMALL, lbl


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctrl: Optional[PicomotorController] = None
        self._is_own_ctrl: bool = False          # True = 직접 연결, False = 주입
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
        root.addStretch(1)

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
        """REFRESH 버튼 — 주입 컨트롤러에서 즉시 조회 (자체 폴링 시는 자동 갱신)."""
        if self._ctrl and self._ctrl.is_connected:
            try:
                self.update_positions(self._ctrl.get_all_positions())
            except Exception as e:
                self.log_message.emit(f"위치 조회 오류: {e}")

    # ── 모터 이동 ─────────────────────────────────────────────────────────────

    def _on_move_requested(self, motor_num: int, steps: int) -> None:
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
        if self._ctrl:
            try:
                self._ctrl.stop_all()
            except Exception:
                pass

    def move(self, motor_num: int, steps: int) -> bool:
        """외부(ScanWorker 등)에서 모터 이동."""
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
        if self._ctrl and self._ctrl.is_connected:
            try:
                return self._ctrl.get_all_positions()
            except Exception:
                pass
        return [None, None, None, None]

    def stop_polling(self) -> None:
        """앱 종료 시 자체 연결만 종료한다. 주입 컨트롤러는 LiveTab이 관리."""
        if self._is_own_ctrl and self._ctrl:
            try:
                self._ctrl.stop_polling()
                self._ctrl.disconnect()
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        return self._ctrl is not None and self._ctrl.is_connected

    @property
    def controller(self) -> Optional[PicomotorController]:
        return self._ctrl
