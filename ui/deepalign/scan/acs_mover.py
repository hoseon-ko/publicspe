"""ACS SPiiPlus 6축 이동 래퍼."""

from __future__ import annotations
import numpy as np


class AcsMover:
    """point: array-like of 6 floats = absolute targets.

    enable/disable는 호출자(스캔 부트스트랩)가 책임진다.
    move(point)는 6축 동시 move_to(wait=False) → wait_in_position_all 패턴.
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
