"""[임시 성능 계측] LIVE UI 블로킹 진단 전용 모듈.

여러 파일(workers / pipeline / image_metrics / viewer_main)이 perf_tick 을 공유하기 위해
순환 import 를 피하려고 중립 위치에 둔다. 진단이 끝나면 이 파일과 각 호출부를 전부 제거할 것.
모든 호출부에는 `[임시 계측]` 주석이 달려 있어 grep 으로 일괄 식별 가능.
"""

from __future__ import annotations

from core.logger import dev_logger

_PERF: dict[str, list] = {}
_PERF_WINDOW = 10  # N프레임마다 평균/최대 로그 1줄 (라이브가 ~0.5fps라 작게 잡음)


def _emit(name: str, acc: list) -> None:
    if acc[0] <= 0:
        return
    dev_logger.info(
        f"[LIVE-PERF] {name:<26} avg={acc[1] / acc[0]:7.2f}ms  max={acc[2]:7.2f}ms  (n={acc[0]})"
    )


def perf_tick(name: str, ms: float) -> None:
    """구간 소요시간(ms) 누적 → N프레임마다 [LIVE-PERF] 로그 1줄. 스레드 안전(logging)."""
    acc = _PERF.setdefault(name, [0, 0.0, 0.0])  # [count, sum, max]
    acc[0] += 1
    acc[1] += ms
    if ms > acc[2]:
        acc[2] = ms
    if acc[0] >= _PERF_WINDOW:
        _emit(name, acc)
        _PERF[name] = [0, 0.0, 0.0]


def perf_flush() -> None:
    """잔여 버킷을 강제로 모두 로그에 내보내고 리셋. 라이브 정지 시 호출."""
    for name in list(_PERF.keys()):
        acc = _PERF[name]
        if acc[0] > 0:
            _emit(f"{name}(flush)", acc)
            _PERF[name] = [0, 0.0, 0.0]
