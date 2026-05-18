"""Picomotor 8742 이동 래퍼 (session_hub 경유)."""

from __future__ import annotations
from typing import Optional


class MirrorMover:
    """Picomotor 8742 래퍼.

    point: tuple[int, int] = (motor_1based, target_steps_absolute)
    내부적으로 현재 위치를 hub에서 읽어 delta를 계산한 뒤 pico_move_relative 호출.
    move() 끝에 pico_wait_motion_done 으로 정지 확인 — timeout 만료 시 TimeoutError.
    """

    def __init__(self, session_hub, move_timeout_ms: int = 10000):
        self._hub = session_hub
        self._move_timeout_ms = int(move_timeout_ms)

    def move(self, point: tuple[int, int]) -> None:
        motor, target = int(point[0]), int(point[1])
        cur = self._hub.pico_get_position(motor)
        if cur is None:
            raise RuntimeError(f"Picomotor M{motor} 위치 조회 실패")
        delta = target - int(cur)
        if delta == 0:
            return
        self._hub.pico_move_relative(motor, delta)
        self._hub.pico_wait_motion_done(motor, self._move_timeout_ms)

    def current(self, motor: int = 1) -> Optional[int]:
        return self._hub.pico_get_position(int(motor))
