"""DeepAlign용 카드 wrapper 패키지.

ui/widgets/의 PicoCard / AcsCard를 composition으로 감싸 DeepAlign 전용 공개 API를
보충한다. 카드 자체는 수정하지 않음 — MotionTab과 동일 위젯을 공유.
"""

from ui.deepalign.wrappers.deepalign_acs_panel    import DeepAlignAcsPanel
from ui.deepalign.wrappers.deepalign_mirror_panel import DeepAlignMirrorPanel

__all__ = ["DeepAlignAcsPanel", "DeepAlignMirrorPanel"]
