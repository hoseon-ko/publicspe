"""KIMM Z 이동 래퍼 (session_hub 경유)."""

from __future__ import annotations


class KimmMover:
    """point: float = absolute Z (µm)."""

    def __init__(self, session_hub):
        self._hub = session_hub

    def move(self, point: float) -> None:
        self._hub.kimm_move_to_z(float(point))

    def current(self) -> float:
        return float(self._hub.kimm_get_z())
