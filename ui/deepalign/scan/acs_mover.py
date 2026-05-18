"""ACS SPiiPlus 6축 이동 래퍼 (session_hub 경유)."""

from __future__ import annotations
import numpy as np


class AcsMover:
    """point: array-like of 6 floats = absolute targets (mm/mrad).

    enable/disable는 호출자(스캔 부트스트랩)가 책임진다.
    move(point)는 6축 동시 acs_move_to → acs_wait_in_position_all 패턴.
    timeout 만료 시 hub.acs_wait_in_position_all 이 TimeoutError 를 raise.

    안전:
    - move 중간 axis 실패 시 acs_stop_all() 호출 → 이미 명령된 축의 폭주 방지
    - dry_run 컨트롤러는 wait가 false-positive 통과를 일으키므로 명시적 예외
    """

    _MOTOR_NAMES = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]

    def __init__(self, session_hub, move_timeout_ms: int = 30000):
        self._hub = session_hub
        self._move_timeout_ms = int(move_timeout_ms)

    def _is_dry_run(self) -> bool:
        ctrl = getattr(self._hub, "acs_controller", None)
        return bool(ctrl is not None and getattr(ctrl, "dry_run", False))

    def enable(self, timeout_ms: int = 1000) -> None:
        if self._is_dry_run():
            return
        self._hub.acs_enable_all()
        if not self._hub.acs_wait_for_enabled_all(timeout_ms=timeout_ms):
            raise RuntimeError("ACS Servo ON 확인 실패 (Timeout)")

    def disable(self) -> None:
        if self._is_dry_run():
            return
        self._hub.acs_disable_all()

    def move(self, point) -> None:
        if self._is_dry_run():
            raise RuntimeError(
                "ACS dry_run 모드: 실제 이동이 발생하지 않아 스캔을 진행하지 않음"
            )

        targets = np.asarray(point, dtype=float).reshape(-1)
        if targets.size != 6:
            raise ValueError(f"ACS point는 6개 값이어야 함 (got {targets.size})")

        # 명령 emit 단계 — 한 축이라도 실패하면 이미 명령된 축을 즉시 정지
        sent_count = 0
        try:
            for i, t in enumerate(targets):
                self._hub.acs_move_to(i, float(t))
                sent_count += 1
        except Exception as exc:
            if sent_count > 0:
                try:
                    self._hub.acs_stop_all()
                except Exception:
                    pass
            raise RuntimeError(
                f"ACS move 명령 실패 (axis={sent_count} 전송 후): {exc}"
            ) from exc

        # In-position 대기 — timeout 시 stop_all 호출 후 TimeoutError 재전파
        try:
            self._hub.acs_wait_in_position_all(timeout_ms=self._move_timeout_ms)
        except TimeoutError:
            try:
                self._hub.acs_stop_all()
            except Exception:
                pass
            raise

    def current(self) -> np.ndarray:
        return np.asarray(self._hub.acs_get_positions(), dtype=float)
