"""DeepAlign용 ACS 패널 wrapper.

ui/widgets/acs_card.py:AcsCard를 composition으로 감싸 DeepAlign이 기대하는 공개
API(enable_all/stop_all/run/get_baseline_dof 등)를 보충한다.
또한 안전 기능인 auto-disable timer(5분 idle 후 서보 OFF)를 유지한다.

AcsCard 자체는 수정하지 않음.
"""

from __future__ import annotations
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout

from ui.widgets.acs_card import AcsCard


class DeepAlignAcsPanel(QWidget):
    """AcsCard wrapper — DeepAlign 호환 공개 API + auto-disable timer."""

    # AcsCard 시그널을 그대로 전달 (passthrough)
    log_message      = pyqtSignal(str)
    acs_connected    = pyqtSignal(object)
    acs_disconnected = pyqtSignal()

    _AUTO_DISABLE_MS = 5 * 60 * 1000   # 5분 idle 시 자동 서보 OFF

    def __init__(self, parent=None):
        super().__init__(parent)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._card = AcsCard()
        lay.addWidget(self._card)

        # 시그널 passthrough
        self._card.log_message.connect(self.log_message)
        self._card.acs_connected.connect(self.acs_connected)
        self._card.acs_disconnected.connect(self.acs_disconnected)

        # ── Auto-disable timer (안전): kin move/enable 시 재시작, 만료시 disable_all
        self._auto_disable_timer = QTimer(self)
        self._auto_disable_timer.setSingleShot(True)
        self._auto_disable_timer.setInterval(self._AUTO_DISABLE_MS)
        self._auto_disable_timer.timeout.connect(self._on_auto_disable_timeout)

        # 이동/엔에이블 액션 발생 시 타이머 재시작
        self._card.btn_en_all.clicked.connect(self._reset_auto_disable)
        self._card.btn_kin_move.clicked.connect(self._reset_auto_disable)

    # ── DeepAlign main_tab 호환 공개 API ─────────────────────────────────

    def bind_session_hub(self, hub) -> None:
        self._card.bind_session_hub(hub)

    def set_controller(self, ctrl) -> None:
        self._card.set_controller(ctrl)

    def enable_all(self) -> None:
        self._card._on_enable_all()
        self._reset_auto_disable()

    def stop_all(self) -> None:
        self._card._on_stop_all()

    def run(self) -> None:
        """EXECUTE KINEMATIC MOVE — 마스터 바 버튼에서 호출."""
        self._card._on_kin_move()
        self._reset_auto_disable()

    def get_baseline_dof(self) -> list[float]:
        """AcsScanWidget의 baseline 동기화용 — KINEMATIC MOVE 입력 6 DOF."""
        return [float(s.value()) for s in self._card._dof_spins]

    # ── Auto-disable timer 내부 ──────────────────────────────────────────

    def _reset_auto_disable(self) -> None:
        self._auto_disable_timer.start(self._AUTO_DISABLE_MS)

    def _on_auto_disable_timeout(self) -> None:
        """5분 idle 후 자동 서보 OFF (안전)."""
        try:
            self._card._on_disable_all()
            self.log_message.emit("[ACS] Auto-disable: 5분 idle — 서보 OFF")
        except Exception:
            pass

    # ── 내부 카드 노출 (필요시 직접 접근) ─────────────────────────────────

    @property
    def card(self) -> AcsCard:
        return self._card
