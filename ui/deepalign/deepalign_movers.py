"""DeepAlign 스캔용 모터 이동 래퍼.

각 하드웨어(피코모터/KIMM/ACS)의 이동 API를 스캔 워커가 사용할
공통 인터페이스(`move(point)` / `current()`)로 감싼다.
워커는 hardware-agnostic하게 mover.move(point)만 호출하면 된다.
"""

from __future__ import annotations

from typing import Any, Callable, Optional
import numpy as np


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


class KimmMover:
    """KIMM Z 래퍼 (session_hub 경유).

    point: float = absolute Z (µm)
    """

    def __init__(self, session_hub):
        self._hub = session_hub

    def move(self, point: float) -> None:
        self._hub.kimm_move_to_z(float(point))

    def current(self) -> float:
        return float(self._hub.kimm_get_z())


class AcsMover:
    """ACS 6축 래퍼.

    point: array-like of 6 floats = absolute targets (mm/rad per axis spec)
    enable_all → 6축 동시 move_to(wait=False) → wait_in_position_all
    enable/disable는 스캔 호출자가 책임 (start_scan/end_scan).
    """

    _MOTOR_NAMES = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]

    def __init__(self, ctrl):
        self._ctrl = ctrl

    def enable(self, timeout_ms: int = 1000) -> None:
        self._ctrl.enable_all()
        if not self._ctrl.wait_for_enabled_all(timeout_ms=timeout_ms):
            raise RuntimeError("ACS Servo ON 확인 실패 (Timeout)")

    def disable(self) -> None:
        self._ctrl.disable_all()

    def move(self, point) -> None:
        targets = np.asarray(point, dtype=float).reshape(-1)
        if targets.size != 6:
            raise ValueError(f"ACS point는 6개 값이어야 함 (got {targets.size})")
        for i, t in enumerate(targets):
            self._ctrl.move_to(i, float(t), wait=False)
        self._ctrl.wait_in_position_all(timeout_ms=30000)

    def current(self) -> np.ndarray:
        return np.array([self._ctrl.get_position(i) for i in range(6)], dtype=float)
