"""ACS SPiiPlus 6축 이동 래퍼 (session_hub 경유)."""

from __future__ import annotations
import numpy as np


class AcsMover:
    """point: array-like of 6 floats = absolute targets (mm/mrad).

    enable/disable는 호출자(스캔 부트스트랩)가 책임진다.
    move(point)는 6축 동시 acs_move_to → acs_wait_in_position_all 패턴.
    timeout 만료 시 hub.acs_wait_in_position_all 이 TimeoutError 를 raise.
    """

    _MOTOR_NAMES = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]

    def __init__(self, session_hub, move_timeout_ms: int = 30000):
        self._hub = session_hub
        self._move_timeout_ms = int(move_timeout_ms)

    def enable(self, timeout_ms: int = 1000) -> None:
        self._hub.acs_enable_all()
        if not self._hub.acs_wait_for_enabled_all(timeout_ms=timeout_ms):
            raise RuntimeError("ACS Servo ON 확인 실패 (Timeout)")

    def disable(self) -> None:
        self._hub.acs_disable_all()

    def move(self, point) -> None:
        targets = np.asarray(point, dtype=float).reshape(-1)
        if targets.size != 6:
            raise ValueError(f"ACS point는 6개 값이어야 함 (got {targets.size})")
        for i, t in enumerate(targets):
            self._hub.acs_move_to(i, float(t))
        self._hub.acs_wait_in_position_all(timeout_ms=self._move_timeout_ms)

    def current(self) -> np.ndarray:
        return np.asarray(self._hub.acs_get_positions(), dtype=float)
