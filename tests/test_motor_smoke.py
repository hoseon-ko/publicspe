"""모터 신뢰성 패치 스모크 테스트.

하드웨어/Qt App 없이 동작하는 단위 검증:
- 3종 mover의 hub 호출 시퀀스
- TimeoutError 전파
- PicomotorController.wait_motion_done position-stability/timeout 로직

실행:
    python tests/test_motor_smoke.py
"""

from __future__ import annotations
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ────────────────────────────────────────────────────────────────────────────
# 결과 집계
# ────────────────────────────────────────────────────────────────────────────
PASS, FAIL = [], []


def case(name):
    def _wrap(fn):
        def _run():
            try:
                fn()
                PASS.append(name)
                print(f"  [OK] {name}")
            except AssertionError as e:
                FAIL.append((name, f"assert: {e}"))
                print(f"  [FAIL] {name}: {e}")
            except Exception as e:
                FAIL.append((name, f"{type(e).__name__}: {e}"))
                print(f"  [ERR ] {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
        return _run
    return _wrap


# ────────────────────────────────────────────────────────────────────────────
# Mover 시그니처/호출 검증
# ────────────────────────────────────────────────────────────────────────────

@case("MirrorMover: hub 시퀀스 (get→move_relative→wait_motion_done)")
def t_mirror_seq():
    from ui.deepalign.scan.mirror_mover import MirrorMover
    hub = MagicMock()
    hub.pico_get_position.return_value = 100
    m = MirrorMover(hub, move_timeout_ms=5000)
    m.move((2, 350))   # M2, target abs=350, cur=100, delta=+250

    hub.pico_get_position.assert_called_once_with(2)
    hub.pico_move_relative.assert_called_once_with(2, 250)
    hub.pico_wait_motion_done.assert_called_once_with(2, 5000)


@case("MirrorMover: delta=0이면 move/wait 호출 없음")
def t_mirror_zero_delta():
    from ui.deepalign.scan.mirror_mover import MirrorMover
    hub = MagicMock()
    hub.pico_get_position.return_value = 500
    m = MirrorMover(hub, move_timeout_ms=5000)
    m.move((1, 500))

    hub.pico_move_relative.assert_not_called()
    hub.pico_wait_motion_done.assert_not_called()


@case("MirrorMover: hub TimeoutError 그대로 전파")
def t_mirror_timeout_propagate():
    from ui.deepalign.scan.mirror_mover import MirrorMover
    hub = MagicMock()
    hub.pico_get_position.return_value = 0
    hub.pico_wait_motion_done.side_effect = TimeoutError("test timeout")
    m = MirrorMover(hub, move_timeout_ms=1000)
    try:
        m.move((1, 999))
    except TimeoutError as e:
        assert "test timeout" in str(e)
        return
    raise AssertionError("TimeoutError 가 전파되지 않음")


@case("KimmMover: done_timeout_s 변환 (ms→s)")
def t_kimm_seq():
    from ui.deepalign.scan.kimm_mover import KimmMover
    hub = MagicMock()
    m = KimmMover(hub, move_timeout_ms=12500)
    m.move(123.456)

    hub.kimm_move_to_z.assert_called_once_with(123.456, done_timeout_s=12.5)


@case("KimmMover: TimeoutError 전파")
def t_kimm_timeout():
    from ui.deepalign.scan.kimm_mover import KimmMover
    hub = MagicMock()
    hub.kimm_move_to_z.side_effect = TimeoutError("kimm done timeout")
    m = KimmMover(hub, move_timeout_ms=1000)
    try:
        m.move(10.0)
    except TimeoutError:
        return
    raise AssertionError("TimeoutError 미전파")


@case("AcsMover: enable→servo wait→move_to×6→wait_in_position_all 시퀀스")
def t_acs_seq():
    import numpy as np
    from ui.deepalign.scan.acs_mover import AcsMover
    hub = MagicMock()
    hub.acs_controller = None   # dry_run 가드 우회
    hub.acs_wait_for_enabled_all.return_value = True
    m = AcsMover(hub, move_timeout_ms=20000)
    m.enable(timeout_ms=1500)
    hub.acs_enable_all.assert_called_once()
    hub.acs_wait_for_enabled_all.assert_called_once_with(timeout_ms=1500)

    pt = np.array([1, 2, 3, 4, 5, 6], dtype=float)
    m.move(pt)
    assert hub.acs_move_to.call_count == 6
    for i in range(6):
        args, _ = hub.acs_move_to.call_args_list[i]
        assert args == (i, float(i + 1)), f"axis {i} 호출 인자 불일치: {args}"
    hub.acs_wait_in_position_all.assert_called_once_with(timeout_ms=20000)


@case("AcsMover: enable 실패 시 RuntimeError")
def t_acs_enable_fail():
    from ui.deepalign.scan.acs_mover import AcsMover
    hub = MagicMock()
    hub.acs_controller = None
    hub.acs_wait_for_enabled_all.return_value = False
    m = AcsMover(hub, move_timeout_ms=10000)
    try:
        m.enable(timeout_ms=500)
    except RuntimeError as e:
        assert "Servo ON" in str(e)
        return
    raise AssertionError("RuntimeError 미발생")


@case("AcsMover: point 크기 검증 (6 != n)")
def t_acs_point_size():
    from ui.deepalign.scan.acs_mover import AcsMover
    hub = MagicMock()
    hub.acs_controller = None
    m = AcsMover(hub, move_timeout_ms=10000)
    try:
        m.move([1, 2, 3])
    except ValueError as e:
        assert "6" in str(e)
        return
    raise AssertionError("ValueError 미발생")


@case("AcsMover: dry_run 컨트롤러 → move()는 명시 RuntimeError")
def t_acs_dry_run_blocks_move():
    from ui.deepalign.scan.acs_mover import AcsMover
    hub = MagicMock()
    hub.acs_controller = MagicMock(dry_run=True)
    m = AcsMover(hub, move_timeout_ms=10000)
    try:
        m.move([0, 0, 0, 0, 0, 0])
    except RuntimeError as e:
        assert "dry_run" in str(e)
        # dry_run 일 땐 hub 명령이 한 번도 emit 되면 안 됨
        hub.acs_move_to.assert_not_called()
        hub.acs_wait_in_position_all.assert_not_called()
        return
    raise AssertionError("RuntimeError 미발생")


@case("AcsMover: dry_run 컨트롤러 → enable/disable no-op")
def t_acs_dry_run_enable_noop():
    from ui.deepalign.scan.acs_mover import AcsMover
    hub = MagicMock()
    hub.acs_controller = MagicMock(dry_run=True)
    m = AcsMover(hub, move_timeout_ms=10000)
    m.enable(timeout_ms=500)
    m.disable()
    hub.acs_enable_all.assert_not_called()
    hub.acs_disable_all.assert_not_called()


@case("AcsMover: 중간 axis 실패 시 acs_stop_all 호출")
def t_acs_partial_failure_stops():
    from ui.deepalign.scan.acs_mover import AcsMover
    hub = MagicMock()
    hub.acs_controller = None
    # 4번째 axis (index 3) 호출에서 실패하도록 설정
    call_log = []
    def _move_to(axis, pos):
        call_log.append(axis)
        if axis == 3:
            raise ValueError(f"Limit Violation: Axis{axis}")
    hub.acs_move_to.side_effect = _move_to

    m = AcsMover(hub, move_timeout_ms=10000)
    try:
        m.move([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    except RuntimeError as e:
        assert "axis=3 전송 후" in str(e)
        hub.acs_stop_all.assert_called_once()
        hub.acs_wait_in_position_all.assert_not_called()
        return
    raise AssertionError("RuntimeError 미발생")


@case("AcsMover: wait timeout 시 acs_stop_all 후 재전파")
def t_acs_wait_timeout_stops():
    from ui.deepalign.scan.acs_mover import AcsMover
    hub = MagicMock()
    hub.acs_controller = None
    hub.acs_wait_in_position_all.side_effect = TimeoutError("test timeout")
    m = AcsMover(hub, move_timeout_ms=1000)
    try:
        m.move([0, 0, 0, 0, 0, 0])
    except TimeoutError:
        hub.acs_stop_all.assert_called_once()
        assert hub.acs_move_to.call_count == 6
        return
    raise AssertionError("TimeoutError 미전파")


# ────────────────────────────────────────────────────────────────────────────
# PicomotorController.wait_motion_done 로직
# ────────────────────────────────────────────────────────────────────────────

class _FakeCmdLib:
    """GetPosition 호출 응답을 시퀀스로 제어하는 가짜 SDK."""
    def __init__(self, positions):
        self._positions = list(positions)
        self._idx = 0
        self.calls = 0

    def GetPosition(self, key, master, motor, reserved):
        self.calls += 1
        if self._idx < len(self._positions):
            pos = self._positions[self._idx]
            self._idx += 1
        else:
            pos = self._positions[-1]
        return (True, pos)


def _make_controller(positions):
    """연결된 상태의 PicomotorController 를 가짜 cmdlib 으로 구성."""
    from core.motor.picomotor import PicomotorController
    ctrl = PicomotorController()
    ctrl._cmdlib = _FakeCmdLib(positions)
    ctrl._key = "dummy"
    ctrl._master = 1
    ctrl._connected = True
    return ctrl


@case("wait_motion_done: 위치 3회 연속 동일 → 즉시 반환")
def t_wait_stable():
    ctrl = _make_controller([10, 20, 30, 30, 30, 30])
    t0 = time.perf_counter()
    ctrl.wait_motion_done(motor=1, timeout_ms=2000, poll_ms=5, stable_n=3)
    dt_ms = (time.perf_counter() - t0) * 1000
    # 약 6 polls × 5ms = 30ms 예상, 200ms 이하 여유
    assert dt_ms < 500, f"너무 오래 걸림: {dt_ms:.1f}ms"


@case("wait_motion_done: 위치가 계속 변하면 TimeoutError")
def t_wait_timeout():
    # 영원히 증가하는 위치 시퀀스
    ctrl = _make_controller(list(range(0, 100000)))
    t0 = time.perf_counter()
    try:
        ctrl.wait_motion_done(motor=1, timeout_ms=150, poll_ms=10, stable_n=3)
    except TimeoutError as e:
        dt_ms = (time.perf_counter() - t0) * 1000
        assert "M1" in str(e) and "150" in str(e)
        assert 100 < dt_ms < 800, f"timeout 시간 비정상: {dt_ms:.1f}ms"
        return
    raise AssertionError("TimeoutError 미발생")


@case("wait_motion_done: GetPosition 예외 → RuntimeError")
def t_wait_get_position_error():
    from core.motor.picomotor import PicomotorController
    ctrl = PicomotorController()

    class _Broken:
        def GetPosition(self, *a, **kw):
            raise RuntimeError("SDK boom")

    ctrl._cmdlib = _Broken()
    ctrl._key = "dummy"
    ctrl._master = 1
    ctrl._connected = True
    try:
        ctrl.wait_motion_done(motor=2, timeout_ms=500)
    except RuntimeError as e:
        assert "M2" in str(e) and "SDK boom" in str(e)
        return
    raise AssertionError("RuntimeError 미발생")


# ────────────────────────────────────────────────────────────────────────────
# 모듈 import sanity (호환성)
# ────────────────────────────────────────────────────────────────────────────

@case("import: 3종 mover")
def t_import_movers():
    from ui.deepalign.scan.mirror_mover import MirrorMover
    from ui.deepalign.scan.kimm_mover import KimmMover
    from ui.deepalign.scan.acs_mover import AcsMover
    assert MirrorMover and KimmMover and AcsMover


@case("import: hub + HAL")
def t_import_hub():
    from core.session.device_session_hub import DeviceSessionHub
    from core.hal.motion_hal import PicoHal, AcsHal, KimmHal
    assert hasattr(DeviceSessionHub, "pico_wait_motion_done")
    assert hasattr(DeviceSessionHub, "acs_wait_in_position_all")
    assert hasattr(DeviceSessionHub, "acs_wait_for_enabled_all")


@case("L2: AcsStageController._last_states 가 axis 별 독립 dict")
def t_acs_last_states_independent():
    from core.motor.acs_stage import AcsStageController
    ctrl = AcsStageController()
    # 한 축 dict 수정 시 다른 축에 영향 없어야 함
    ctrl._last_states[2]["enabled"] = True
    others = [i for i in range(6) if i != 2]
    for i in others:
        assert ctrl._last_states[i]["enabled"] is False, \
            f"axis {i} 가 axis 2 와 dict 공유됨"


@case("L5: AcsScanWidget sweep spin range 가 ±10 으로 클램프")
def t_acs_widget_sweep_clamp():
    import sys
    sys.modules.setdefault("PyQt6.QtWidgets", __import__("PyQt6.QtWidgets", fromlist=["*"]))
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from ui.deepalign.scan.scan_widgets.acs_scan_widget import AcsScanWidget
    w = AcsScanWidget()
    lo, hi = w.spin_start.minimum(), w.spin_start.maximum()
    assert lo == -10.0 and hi == 10.0, f"sweep range 비정상: [{lo}, {hi}]"


@case("Mover 시그니처: 3종 모두 session_hub 단일 인자 + move_timeout_ms")
def t_signature_consistency():
    import inspect
    from ui.deepalign.scan.mirror_mover import MirrorMover
    from ui.deepalign.scan.kimm_mover import KimmMover
    from ui.deepalign.scan.acs_mover import AcsMover
    for cls in (MirrorMover, KimmMover, AcsMover):
        sig = inspect.signature(cls.__init__)
        params = list(sig.parameters.keys())
        assert "session_hub" in params, f"{cls.__name__}: session_hub 인자 없음 ({params})"
        assert "move_timeout_ms" in params, f"{cls.__name__}: move_timeout_ms 없음 ({params})"


# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("t_")]
    print(f"\n=== Motor Smoke Test ({len(tests)} cases) ===\n")
    for fn in tests:
        fn()
    print(f"\n=== Summary: {len(PASS)} PASS, {len(FAIL)} FAIL ===")
    if FAIL:
        print("\nFailures:")
        for name, err in FAIL:
            print(f"  - {name}: {err}")
        sys.exit(1)
    sys.exit(0)
