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


@case("Scan: _ScanWorkerBase 가 포인트마다 point_done(frame=...) emit (viewer 전달용)")
def t_scan_worker_emits_point_done_with_frame():
    """main_tab._scan_start 가 point_done 을 _push_frame 으로 라우팅하므로,
    워커가 frame 을 포함해 emit 하지 않으면 viewer / processing 파이프라인이
    전혀 동작하지 않는다.
    """
    import numpy as np
    from unittest.mock import MagicMock
    from ui.deepalign.scan._scan_base import _ScanWorkerBase

    mover = MagicMock()
    fake_frame = np.zeros((4, 4), dtype=np.uint16)
    snap_fn = MagicMock(return_value=fake_frame)

    worker = _ScanWorkerBase(mover, snap_fn, points=[1, 2, 3],
                             settle_ms=0, avg_frames=1)

    captured = []
    def _on_point_done(idx, total, point, frame, result):
        captured.append((idx, total, point, frame, result))
    worker.point_done.connect(_on_point_done)

    finished_results = []
    worker.finished.connect(lambda r: finished_results.append(r))

    worker.run()

    assert len(captured) == 3, f"point_done 횟수 비정상: {len(captured)}"
    for idx, (i, total, point, frame, _) in enumerate(captured, 1):
        assert i == idx and total == 3
        assert frame is not None and frame.shape == (4, 4)
    assert mover.move.call_count == 3
    assert snap_fn.call_count == 3
    assert finished_results == [[None, None, None]]   # process_fn 없으면 result=None


@case("Camera restore: _push_saved_camera_settings 가 exposure/temp/ADC/fps 전부 전송")
def t_push_saved_camera_settings_full():
    """수동/자동 connect 양쪽에서 호출되는 공용 helper. config 의 저장값을
    빠짐없이 hub.camera_set_* 로 전달하는지 검증.
    """
    from unittest.mock import MagicMock
    from ui.deepalign.deepalign_camera_hub_mixin import CameraHubMixin

    host = MagicMock()
    host._session_hub = MagicMock()
    cfg = MagicMock()
    host._cfg = cfg

    # vendor 별 저장값 시뮬레이션
    def _get(key, default=None, vendor=None):
        return {
            "exposure_ms": 12.5,
            "fps":         15.0,
            "fps_lock":    True,
            "temp_c":      -72.0,
            "adc.quality": "Low Noise",
            "adc.speed":   "100kHz",
            "adc.gain":    "Low",
            "adc.bit":     "16bit",
        }.get(key, default)
    cfg.get_camera_setting.side_effect = _get

    caps = MagicMock()
    caps.has_fps_control = True
    caps.has_temperature = True
    caps.has_adc = True

    CameraHubMixin._push_saved_camera_settings(host, caps, "Picam")

    h = host._session_hub
    h.camera_set_exposure_ms.assert_called_once()
    assert h.camera_set_exposure_ms.call_args[0][1] == 12.5
    h.camera_set_fps.assert_called_once()
    assert h.camera_set_fps.call_args[0][1] == 15.0
    h.camera_set_temperature.assert_called_once()
    assert h.camera_set_temperature.call_args[0][1] == -72.0
    h.camera_set_adc_settings.assert_called_once()
    kw = h.camera_set_adc_settings.call_args.kwargs
    assert kw == {
        "adc_quality": "Low Noise",
        "adc_speed":   "100kHz",
        "adc_analog_gain": "Low",
        "bit_depth":   "16bit",
    }


@case("Camera restore: fps_lock=False 면 disable_fps_lock 호출")
def t_push_saved_fps_unlock():
    from unittest.mock import MagicMock
    from ui.deepalign.deepalign_camera_hub_mixin import CameraHubMixin

    host = MagicMock()
    host._session_hub = MagicMock()
    cfg = MagicMock()
    host._cfg = cfg
    cfg.get_camera_setting.side_effect = lambda key, default=None, vendor=None: {
        "exposure_ms": 20.0, "fps_lock": False, "temp_c": -70.0,
    }.get(key, default)

    caps = MagicMock(); caps.has_fps_control = True; caps.has_temperature = False; caps.has_adc = False
    CameraHubMixin._push_saved_camera_settings(host, caps, "Picam")

    host._session_hub.camera_set_fps.assert_not_called()
    host._session_hub.camera_disable_fps_lock.assert_called_once()


@case("Camera restore: caps.has_adc=False 면 ADC push 생략")
def t_push_saved_skip_no_adc():
    from unittest.mock import MagicMock
    from ui.deepalign.deepalign_camera_hub_mixin import CameraHubMixin

    host = MagicMock()
    host._session_hub = MagicMock()
    cfg = MagicMock()
    host._cfg = cfg
    cfg.get_camera_setting.side_effect = lambda key, default=None, vendor=None: {
        "exposure_ms": 20.0,
    }.get(key, default)

    caps = MagicMock(); caps.has_fps_control = False; caps.has_temperature = False; caps.has_adc = False
    CameraHubMixin._push_saved_camera_settings(host, caps, "Picam")

    host._session_hub.camera_set_adc_settings.assert_not_called()
    host._session_hub.camera_set_temperature.assert_not_called()
    host._session_hub.camera_set_fps.assert_not_called()
    host._session_hub.camera_set_exposure_ms.assert_called_once()  # exposure 는 caps 무관


@case("Camera restore: 한 항목 실패해도 다른 항목 push 계속")
def t_push_saved_isolation():
    from unittest.mock import MagicMock
    from ui.deepalign.deepalign_camera_hub_mixin import CameraHubMixin

    host = MagicMock()
    host._session_hub = MagicMock()
    host._session_hub.camera_set_exposure_ms.side_effect = RuntimeError("boom")
    cfg = MagicMock()
    host._cfg = cfg
    cfg.get_camera_setting.side_effect = lambda key, default=None, vendor=None: {
        "exposure_ms": 20.0, "temp_c": -70.0,
    }.get(key, default)

    caps = MagicMock(); caps.has_fps_control = False; caps.has_temperature = True; caps.has_adc = False
    CameraHubMixin._push_saved_camera_settings(host, caps, "Picam")

    # exposure 가 실패해도 temp 는 호출돼야 함
    host._session_hub.camera_set_temperature.assert_called_once()


@case("ADC combo populate: clear/addItems 가 _save_settings 트리거 안 함 (blockSignals)")
def t_adc_combo_no_save_trigger():
    """LayoutBuilderMixin._apply_camera_capabilities 가 ADC 콤보를
    clear + addItems 로 채울 때 currentTextChanged → _save_settings 가
    호출되지 않아야 함. 호출되면 사용자 저장값(특히 quality) 가
    콤보 첫 항목으로 덮어 씌워지는 회귀.
    """
    import sys
    from unittest.mock import MagicMock
    from PyQt6.QtWidgets import QApplication, QComboBox
    app = QApplication.instance() or QApplication(sys.argv)

    save_mock = MagicMock()

    class _FakeHost:
        """LayoutBuilderMixin._apply_camera_capabilities 에 필요한 최소 속성만."""
        def __init__(self, save):
            self.cb_adc_quality = QComboBox()
            self.cb_adc_speed   = QComboBox()
            self.cb_adc_gain    = QComboBox()
            self.cb_adc_bit     = QComboBox()
            # 사용자 저장값 시뮬레이션
            self.cb_adc_quality.addItem("Low Noise")
            self.cb_adc_speed.addItem("100kHz")
            self.cb_adc_gain.addItem("Low")
            self.cb_adc_bit.addItem("16bit")
            # 4개 모두 _save_settings 에 연결 (실제 코드와 동일)
            for cb in (self.cb_adc_quality, self.cb_adc_speed,
                       self.cb_adc_gain, self.cb_adc_bit):
                cb.currentTextChanged.connect(save)
            # 필요 부수 위젯 (가시성 toggle 만 호출)
            from PyQt6.QtWidgets import QWidget
            self.sec_fps = QWidget(); self.sec_adc = QWidget(); self.sec_temp = QWidget()
            self.spin_temp = MagicMock()

    class _Caps:
        has_fps_control = False
        has_adc = True
        has_temperature = False
        # 카메라 후보 순서가 사용자 저장값(첫 항목=Low Noise) 과 반대로 옴.
        # 패치 전이면 addItems 후 cb 의 currentText 가 "High Capacity" 로 바뀌어
        # _save_settings 호출 → 저장 손실.
        adc_quality_options    = ["High Capacity", "Low Noise"]
        adc_speed_options      = ["1MHz", "100kHz"]
        adc_gain_options       = ["High", "Low"]
        adc_bit_depth_options  = ["12bit", "16bit"]

    from ui.deepalign.deepalign_layout import LayoutBuilderMixin
    host = _FakeHost(save_mock)
    LayoutBuilderMixin._apply_camera_capabilities(host, _Caps())

    # _save_settings 가 한 번이라도 호출되면 회귀.
    assert save_mock.call_count == 0, (
        f"_save_settings 가 {save_mock.call_count}회 호출됨 — "
        f"ADC 콤보 populate 중 신호 차단 누락. quality 가 덮어 씌워지는 버그 재발."
    )
    # 콤보는 정상적으로 채워져 있어야 함
    assert host.cb_adc_quality.count() == 2
    assert host.cb_adc_speed.count() == 2


@case("Disconnect: AcsCard 이벤트 핸들러는 hub.acs_disconnect 재호출 안 함")
def t_acs_card_event_no_reentry():
    """ACS_DISCONNECTED 이벤트 수신 시 UI cleanup 만 — hub 메서드 재호출 금지."""
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from ui.widgets.acs_card import AcsCard
    card = AcsCard()
    fake_hub = MagicMock()
    fake_hub.is_acs_connected.return_value = False
    card._session_hub = fake_hub

    # ACS_DISCONNECTED 이벤트 simulation
    from core.session.session_events import SessionEventType
    ev = MagicMock()
    ev.event_type = SessionEventType.ACS_DISCONNECTED
    card._on_session_event(ev)

    # 이벤트 핸들러는 절대 hub.acs_disconnect 를 부르면 안 됨 (재진입 방지)
    fake_hub.acs_disconnect.assert_not_called()
    assert card._ctrl_ref[0] is None


@case("Disconnect: AcsCard._on_disconnect 재진입 가드")
def t_acs_card_disconnect_reentry_guard():
    """버튼 핸들러가 같은 콜스택에서 두 번 호출돼도 hub.acs_disconnect 는 1회만."""
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from ui.widgets.acs_card import AcsCard
    card = AcsCard()
    fake_hub = MagicMock()
    fake_hub.is_acs_connected.return_value = False

    # hub.acs_disconnect 가 호출될 때, 같은 콜스택에서 _on_disconnect 가
    # 한 번 더 호출되도록 setup (이벤트 cascade 시뮬레이션)
    def _reentrant(*a, **kw):
        card._on_disconnect()
    fake_hub.acs_disconnect.side_effect = _reentrant

    card._session_hub = fake_hub
    card._on_disconnect()
    # 가드 덕분에 hub.acs_disconnect 는 단 1회만 호출됨
    assert fake_hub.acs_disconnect.call_count == 1


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
