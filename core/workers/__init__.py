"""공용 백그라운드 워커 모음.

LiveTab / DeepAlign / Acquisition 등 여러 탭에서 공유하는 워커들의 단일 출처.
탭별로 워커를 재정의하지 말고 여기서 import 한다.
"""

from core.workers.snap_worker import SnapWorker

__all__ = ["SnapWorker"]
