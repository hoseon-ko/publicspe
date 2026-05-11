"""기존 DeepAlign import 호환용 래퍼 파일.

이 파일은 예전 import 경로를 깨지 않게 유지하기 위한 호환 계층입니다.
실제 canonical 모듈은 ui.deepalign.deepalign_main_tab 입니다.
"""

from ui.deepalign.deepalign_main_tab import DeepAlignMainTab

__all__ = ["DeepAlignMainTab"]
