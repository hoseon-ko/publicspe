"""KIMM Z 이동 래퍼 (session_hub 경유)."""

from __future__ import annotations


class KimmMover:
    """point: float = absolute Z (µm).

    move()는 hub.kimm_move_to_z 가 내부적으로 ACK/DONE 이벤트 대기를 포함하므로
    blocking. move_timeout_ms 는 Done 대기 timeout 으로 전달.
    """

    def __init__(self, session_hub, move_timeout_ms: int = 30000):
        self._hub = session_hub
        self._move_timeout_ms = int(move_timeout_ms)

    def move(self, point: float) -> None:
        self._hub.kimm_move_to_z(
            float(point),
            done_timeout_s=self._move_timeout_ms / 1000.0,
        )

    def current(self) -> float:
        return float(self._hub.kimm_get_z())
