"""DeepAlign용 Mirror(Picomotor) 패널 wrapper.

ui/widgets/pico_card.py:PicoCard를 composition으로 감싸 DeepAlign 호환 공개 API를
보충한다. PicoCard 자체는 수정하지 않음.

NOTE: PicoCard는 hub-only 위젯이라 standalone connect / set_controller 외부 주입은
지원되지 않는다. wrapper의 set_controller/reset_controller는 호환성 유지를 위한
no-op (silent). DeepAlign은 session_hub를 항상 바인딩하므로 실 운용에는 무관.
"""

from __future__ import annotations
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout

from ui.widgets.pico_card import PicoCard
from core.logger import dev_logger


class DeepAlignMirrorPanel(QWidget):
    """PicoCard wrapper — DeepAlign 호환 공개 API."""

    # passthrough
    log_message       = pyqtSignal(str)
    connected         = pyqtSignal(str)
    disconnected      = pyqtSignal()
    positions_updated = pyqtSignal(list)   # PicoCard 미지원 — 호환용

    def __init__(self, parent=None):
        super().__init__(parent)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._card = PicoCard()
        lay.addWidget(self._card)

        # passthrough
        self._card.log_message.connect(self.log_message)

    # ── DeepAlign main_tab 호환 공개 API ─────────────────────────────────

    def bind_session_hub(self, hub) -> None:
        self._card.bind_session_hub(hub)

    def zero_all(self) -> None:
        self._card._on_zero_all_clicked()

    def stop_all(self) -> None:
        self._card._on_stop_all_clicked()

    def reset_controller(self) -> None:
        """기존 MirrorMotorPanel의 호환 stub. PicoCard는 hub-only라 no-op."""
        dev_logger.debug("[DeepAlignMirrorPanel] reset_controller is a no-op (hub-only)")

    def set_controller(self, ctrl) -> None:
        """기존 MirrorMotorPanel의 호환 stub. PicoCard는 hub 경유로 컨트롤러를 받는다."""
        # hub가 이미 바인딩되어 있다면 hub가 컨트롤러 상태를 관리하므로 작업 불필요.
        dev_logger.debug(
            f"[DeepAlignMirrorPanel] set_controller called (ignored — PicoCard uses hub). ctrl={ctrl}"
        )

    # ── 내부 카드 노출 ────────────────────────────────────────────────────

    @property
    def card(self) -> PicoCard:
        return self._card
