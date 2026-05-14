"""
smoke_test.py — MotionHub 시뮬레이션 시퀀스 분석
=====================================================
실제 ACS DLL 없이 MockAcsHal로 전체 모션 시퀀스 검증.

실행:  python smoke_test.py
"""

from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QEventLoop
from PyQt6.QtWidgets import QApplication


# ──────────────────────────────────────────────────────────────────────────────
# Mock ACS HAL
# ──────────────────────────────────────────────────────────────────────────────

class MockAcsHal(QObject):
    """DLL 없이 신호/메서드만 모사하는 가상 ACS HAL."""

    positions_updated = pyqtSignal(list)
    state_updated     = pyqtSignal(list)

    def __init__(self, step_size: float = 50.0, tick_ms: int = 30):
        super().__init__()
        self._positions  = [0.0] * 6
        self._targets    = None
        self._moving     = [False] * 6
        self._enabled    = [False] * 6
        self._move_calls: list = []
        self._step_size  = step_size        # mm/tick: 크게 잡아 빠르게 수렴

        self._tick = QTimer(self)
        self._tick.setInterval(tick_ms)
        self._tick.timeout.connect(self._step)

    def enable_all(self)  -> None: self._enabled = [True]  * 6
    def disable_all(self) -> None: self._enabled = [False] * 6

    def stop_all(self) -> None:
        self._moving = [False] * 6
        self._tick.stop()
        self._emit_state()

    def move_atomic(self, targets: list[float]) -> None:
        self._targets = list(targets)
        self._move_calls.append(list(targets))
        self._moving = [True] * 6
        self._tick.start()
        self._emit_state()

    def get_positions(self) -> list[float]:
        return list(self._positions)

    def disconnect(self) -> None:
        self._tick.stop()

    def _step(self):
        if self._targets is None:
            return
        done = True
        for i, (cur, tgt) in enumerate(zip(self._positions, self._targets)):
            d = tgt - cur
            if abs(d) > 0.001:
                self._positions[i] += min(abs(d), self._step_size) * (1 if d > 0 else -1)
                done = False
            else:
                self._positions[i] = tgt
                self._moving[i] = False

        self.positions_updated.emit(list(self._positions))
        if done:
            self._moving = [False] * 6
            self._tick.stop()
        self._emit_state()

    def _emit_state(self):
        self.state_updated.emit([
            {"enabled": self._enabled[i], "moving": self._moving[i], "in_pos": not self._moving[i]}
            for i in range(6)
        ])


# ──────────────────────────────────────────────────────────────────────────────
# 이벤트 루프 대기 헬퍼 (sleep 없이 Qt 이벤트 처리)
# ──────────────────────────────────────────────────────────────────────────────

def wait_until(condition_fn, timeout_ms: int = 3000) -> bool:
    """condition_fn()이 True가 될 때까지 Qt 이벤트를 처리하며 대기."""
    loop = QEventLoop()
    result = [False]

    def check():
        if condition_fn():
            result[0] = True
            loop.quit()

    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(check)
    timer.start()

    killer = QTimer()
    killer.setSingleShot(True)
    killer.timeout.connect(loop.quit)
    killer.start(timeout_ms)

    loop.exec()
    timer.stop()
    return result[0]


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 본체
# ──────────────────────────────────────────────────────────────────────────────

class MotionSequenceTest(QObject):

    def __init__(self):
        super().__init__()
        self._pass: list[str] = []
        self._fail: list[str] = []
        self._events: list[str] = []

    def ok(self, label: str):
        self._pass.append(label)
        print(f"  ✅  {label}", flush=True)

    def fail(self, label: str):
        self._fail.append(label)
        print(f"  ❌  {label}", flush=True)

    def evt(self, tag: str, *args):
        msg = f"[{tag}] " + " | ".join(str(a) for a in args)
        self._events.append(msg)
        print(f"       {msg}", flush=True)

    def run(self):
        from core.hal.motion_hub import MotionHub, MotionState
        from core.hal.errors import HalCommandError
        from core.motor.kinematic_calc import KinematicCalc, is_available

        print("=" * 64, flush=True)
        print("  MotionHub 시뮬레이션 시퀀스 분석", flush=True)
        print("=" * 64, flush=True)

        hub  = MotionHub(settle_ms=200)   # 빠른 settle 타임
        mock = MockAcsHal(step_size=100.0, tick_ms=20)

        # 신호 구독
        hub.state_changed.connect(lambda s: self.evt("STATE", s.value))
        hub.move_started.connect(lambda: self.evt("MOVE_STARTED"))
        hub.move_finished.connect(
            lambda ok, msg: self.evt("MOVE_FINISHED", "✓ OK" if ok else "✗ FAIL", msg)
        )
        hub.joint_updated.connect(
            lambda j: self.evt("JOINT", [f"{v:.2f}" for v in j])
        )
        hub.cartesian_updated.connect(
            lambda c: self.evt("CARTESIAN", [f"{v:.4f}" for v in c])
        )

        # ── TEST 1: HAL attach ────────────────────────────────────────
        print("\n[TEST 1] MockAcsHal → MotionHub attach", flush=True)
        hub.attach_acs(mock)
        assert hub._acs_hal is mock
        assert hub.state == MotionState.LOCKED
        self.ok("HAL attach + 초기 상태 LOCKED")

        # ── TEST 2: 위치 업데이트 → list copy 보호 ───────────────────
        print("\n[TEST 2] update_joint_positions — list copy 보호", flush=True)
        orig = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        hub.update_joint_positions(orig)
        orig[0] = 9999.0
        if hub.current_joints[0] != 9999.0:
            self.ok("list copy 보호 (외부 리스트 변경 영향 없음)")
        else:
            self.fail("list copy 미적용 — 참조 저장 버그")

        # ── TEST 3: IK 가용 여부 확인 ────────────────────────────────
        print("\n[TEST 3] AlignStageAlgorithm (IK) 가용 여부", flush=True)
        ik_ok = is_available()
        print(f"  IK 모듈: {'사용 가능' if ik_ok else '없음'}", flush=True)
        self.ok(f"IK {'사용 가능' if ik_ok else '없음 — FK 전용 모드'}")

        if ik_ok:
            calc = KinematicCalc()
            targets_ik, ball, ok_ik, viol = calc.calculate([0, 0, 0], [0, 0, 0])
            print(f"  IK(원점→원점): ok={ok_ik}  targets={targets_ik.tolist()}", flush=True)
            print(f"  violations: {viol}", flush=True)
            if ok_ik:
                self.ok("IK 원점 계산 통과")
            else:
                self.fail(f"IK 원점 계산 실패: {viol}")

        # ── TEST 4: 인터록 — IK 실패 시 상태 보호 ───────────────────
        print("\n[TEST 4] 인터록 — 잘못된 목표값 처리", flush=True)
        if not ik_ok:
            # IK 모듈 없음 → 무조건 인터록
            try:
                hub.move_to_cartesian(0, 0, 0, 0, 0, 0)
                self.fail("인터록 미발동 (HalCommandError 기대)")
            except HalCommandError as e:
                assert hub.state == MotionState.LOCKED
                self.ok(f"IK 없음 인터록 정상 (LOCKED 유지) → {e}")
        else:
            # IK 있음 → 소프트리밋 위반으로 인터록 테스트
            try:
                hub.move_to_cartesian(999999, 0, 0, 0, 0, 0)
                self.fail("소프트리밋 위반 통과됨")
            except HalCommandError as e:
                assert hub.state == MotionState.LOCKED
                self.ok(f"소프트리밋 인터록 정상 (LOCKED 유지)")

        # ── TEST 5: 상태 머신 LOCKED→MOVING→SETTLING→LOCKED ─────────
        print("\n[TEST 5] 상태 머신 전이 시뮬레이션", flush=True)
        mock.enable_all()

        # 현재 위치와 가까운 목표 (step_size=100이면 20ms 안에 수렴)
        near_targets = [0.1, 0.2, 0.3, 0.1, 0.2, 0.3]

        # 상태를 MOVING으로 강제 설정 후 move_atomic 트리거
        hub._set_state(MotionState.MOVING)
        hub.move_started.emit()
        mock.move_atomic(near_targets)

        # SETTLING 대기 (최대 2초)
        reached_settling = wait_until(
            lambda: hub.state in (MotionState.SETTLING, MotionState.LOCKED),
            timeout_ms=2000
        )
        print(f"  이동 완료 후 상태: {hub.state.value}", flush=True)
        if reached_settling:
            self.ok(f"MOVING → {hub.state.value} 전이 (move 완료 감지)")
        else:
            self.fail(f"SETTLING 도달 실패 (타임아웃), 현재: {hub.state.value}")

        # LOCKED 대기 (settle timer 200ms)
        reached_locked = wait_until(
            lambda: hub.state == MotionState.LOCKED,
            timeout_ms=1000
        )
        if reached_locked:
            self.ok("SETTLING → LOCKED 전이 (settle timer 동작)")
        else:
            self.fail(f"LOCKED 도달 실패, 현재: {hub.state.value}")

        # ── TEST 6: move_atomic 호출 기록 ────────────────────────────
        print("\n[TEST 6] move_atomic 호출 기록 확인", flush=True)
        n = len(mock._move_calls)
        if n >= 1:
            self.ok(f"move_atomic {n}회 호출 확인")
            for i, call in enumerate(mock._move_calls):
                print(f"  call[{i}]: {[f'{v:.4f}' for v in call]}", flush=True)
        else:
            self.fail("move_atomic 호출 없음")

        # ── TEST 7: FK 계산 신호 체인 ────────────────────────────────
        print("\n[TEST 7] FK 신호 체인 (positions_updated → cartesian_updated)", flush=True)
        cartesian_received = [None]
        hub.cartesian_updated.connect(lambda c: cartesian_received.__setitem__(0, c))

        # 위치 주입 (인코더 기준 원점)
        enc_pos = [1277.5, -1513.68, 804.45, -1592.31, 1052.41, -1433.52]
        mock._positions = enc_pos[:]
        mock.positions_updated.emit(enc_pos)

        QApplication.processEvents()

        if ik_ok and cartesian_received[0] is not None:
            print(f"  FK 결과: {[f'{v:.4f}' for v in cartesian_received[0]]}", flush=True)
            self.ok("FK 계산 후 cartesian_updated 신호 수신")
        elif not ik_ok:
            self.ok("FK 모듈 없음 — cartesian_updated 미발생 (정상)")
        else:
            self.fail("cartesian_updated 신호 미수신")

        # ── TEST 8: sync_positions (HAL에서 직접 읽기) ───────────────
        print("\n[TEST 8] sync_positions() — HAL→Hub 위치 동기화", flush=True)
        mock._positions = [1.1, 2.2, 3.3, 4.4, 5.5, 6.6]
        hub.sync_positions()
        QApplication.processEvents()
        assert hub.current_joints == pytest_approx_list(mock._positions, tol=1e-6)
        self.ok(f"sync_positions 정상 — joints={hub.current_joints}")

        # ── 최종 보고 ────────────────────────────────────────────────
        self._report()
        QApplication.quit()

    def _report(self):
        total = len(self._pass) + len(self._fail)
        print("\n" + "─" * 64, flush=True)
        print(f"  이벤트 로그 ({len(self._events)}건)", flush=True)
        for e in self._events:
            print(f"    {e}", flush=True)
        print("─" * 64, flush=True)
        print(f"\n  결과: {len(self._pass)}/{total} 통과", flush=True)
        if self._fail:
            print("  실패:", flush=True)
            for f in self._fail:
                print(f"    • {f}", flush=True)
        print("=" * 64, flush=True)


def pytest_approx_list(a, b=None, tol=1e-4):
    """두 list가 tol 이내인지 확인 (assert용)."""
    if b is None:
        return a   # assert 연산자에서 사용 시 b를 직접 비교
    return all(abs(x - y) < tol for x, y in zip(a, b))


# ──────────────────────────────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    test = MotionSequenceTest()
    QTimer.singleShot(0, test.run)
    sys.exit(app.exec())
