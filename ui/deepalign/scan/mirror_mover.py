"""Picomotor 8742 이동 래퍼."""

from __future__ import annotations
from typing import Optional


class MirrorMover:
    """Picomotor 8742 래퍼.

    point: tuple[int, int] = (motor_1based, target_steps_absolute)
    내부적으로 current pos를 읽어 delta를 계산한 뒤 move_relative 호출.
    """

    def __init__(self, ctrl):
        self._ctrl = ctrl

    def move(self, point: tuple[int, int]) -> None:
        motor, target = int(point[0]), int(point[1])
        cur = self._ctrl.get_position(motor)
        if cur is None:
            raise RuntimeError(f"Picomotor M{motor} 위치 조회 실패")
        delta = target - int(cur)
        if delta != 0:
            ok = self._ctrl.move_relative(motor, delta)
            if not ok:
                raise RuntimeError(f"Picomotor M{motor} move_relative 실패 ({delta:+d} steps)")

    def current(self, motor: int = 1) -> Optional[int]:
        return self._ctrl.get_position(int(motor))
