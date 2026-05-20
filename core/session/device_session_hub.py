"""Device session hub.

모든 하드웨어 접근의 단일 진입점.
- 카메라 작업은 _camera_lock (RLock) 으로 직렬화
- 상태 변경 및 이벤트 발행은 Qt 신호를 통해 UI 스레드에서 안전하게 처리
"""

from __future__ import annotations

from dataclasses import asdict
from threading import RLock
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core.hal.camera_hal import CameraDeviceInfo, CameraHal
from core.hal.errors import HalCommandError, HalNotConnectedError
from core.hal.motion_hal import AcsHal, KimmHal, PicoHal
from core.hal.motion_hub import MotionHub
from core.logger import dev_logger
from core.session.ownership import OWNER_NONE, validate_owner
from core.session.session_events import SessionEvent, SessionEventType, make_event
from core.session.session_state import (
    ActivityState,
    CameraConnectionState,
    DeviceConnectionState,
    SessionState,
    StreamState,
    create_default_state,
)


class DeviceSessionHub(QObject):
    event_published = pyqtSignal(object)   # SessionEvent
    status_message  = pyqtSignal(str)
    frame_ready     = pyqtSignal(object, object)  # rgb, raw

    # ── 집중 관리 폴링 시그널 ────────────────────────────────────────────
    pico_positions_updated    = pyqtSignal(list)           # [p1, p2, p3, p4] (int)
    camera_temperature_updated = pyqtSignal(object, object, object)  # reading, setpoint, status
    acs_positions_updated     = pyqtSignal(list)           # [j1, j2, j3, j4, j5, j6] (float)
    acs_states_updated        = pyqtSignal(list)           # [dict, dict, ...] (axis states)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: SessionState = create_default_state()
        self._camera_hal: CameraHal | None = None
        self._camera_factories: dict[str, Callable[[], CameraHal]] = {}
        self._acs_hal:  AcsHal  | None = None
        self._kimm_hal: KimmHal | None = None
        self._pico_hal: PicoHal | None = None
        self._motion_hub: MotionHub = MotionHub()
        self._setup_motion_hub_bindings()
        self._last_frame = None
        self._camera_lock = RLock()  # 카메라 관련 작업 직렬화

        # ── Hub-side 폴링 타이머 (UI QTimer 대신 Hub에서 단일 관리) ──────
        self._pico_poll_timer: object | None = None   # QTimer (lazy import 방지)
        self._temp_poll_timer: object | None = None

    # ──────────────────────────────────────────────────────────
    # 카메라 등록 / 스캔
    # ──────────────────────────────────────────────────────────

    def register_camera_hal(self, vendor: str, factory: Callable[[], CameraHal]) -> None:
        key = vendor.strip().lower()
        if not key:
            raise ValueError("Vendor must not be empty")
        self._camera_factories[key] = factory

    def select_camera_vendor(self, vendor: str) -> None:
        key = vendor.strip().lower()
        self._state.camera.vendor = key

    def scan_cameras(self) -> list[CameraDeviceInfo]:
        key = self._state.camera.vendor.strip().lower()
        if not key:
            return []
        factory = self._camera_factories.get(key)
        if factory is None:
            self.publish_status(f"Camera vendor is not registered: {key}", source="hub")
            return []
        hal = factory()
        try:
            return hal.list_devices(key)
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] scan_cameras failed vendor={key}")
            self.publish_error("camera", f"scan failed: {exc}", source="hub")
            return []
        finally:
            try:
                hal.disconnect()
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────
    # 카메라 연결 / 해제
    # ──────────────────────────────────────────────────────────

    def connect_camera(self, device_id: str) -> None:
        with self._camera_lock:
            key = self._state.camera.vendor.strip().lower()
            if not key:
                raise ValueError("Camera vendor is not selected")
            factory = self._camera_factories.get(key)
            if factory is None:
                raise ValueError(f"Camera vendor is not registered: {key}")

            if self._camera_hal is not None:
                self.disconnect_camera(reason="switching camera")

            self._state.camera.connection = CameraConnectionState.CONNECTING
            self.set_camera_device(device_id)
            self.publish_status(
                f"Connecting camera: vendor={key}, device={device_id}", source="hub"
            )

            hal = factory()
            try:
                hal.connect(device_id)
                self._camera_hal = hal
                self.mark_camera_connected(source="hub")
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] connect_camera failed vendor={key}, device_id={device_id}"
                )
                self._state.camera.connection = CameraConnectionState.ERROR
                self.publish_error("camera", f"connect failed: {exc}", source="hub")
                raise

    def disconnect_camera(self, reason: str = "") -> None:
        with self._camera_lock:
            hal = self._camera_hal
            self._camera_hal = None
            if hal is not None:
                try:
                    hal.disconnect()
                except Exception as exc:
                    dev_logger.exception("[DeviceSessionHub] disconnect_camera failed")
                    self.publish_error("camera", f"disconnect failed: {exc}", source="hub")
            self.mark_camera_disconnected(reason=reason, source="hub")

    # ──────────────────────────────────────────────────────────
    # 스트리밍  [FIXED: _camera_lock 보호 추가]
    # ──────────────────────────────────────────────────────────

    def start_stream(
        self, owner: str, frame_cb: Callable[[object], None] | None = None
    ) -> None:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()

            if self._state.camera.stream == StreamState.STREAMING:
                raise HalCommandError("Stream already running")
            if (
                self._state.activity.acquisition == ActivityState.RUNNING
                and self._state.exclusive_owner != normalized
            ):
                raise HalCommandError(
                    "Cannot start stream during acquisition by another owner"
                )

            try:
                if frame_cb is None:
                    def _default_frame_cb(frame):
                        self.publish_frame(frame, frame, source="hub")
                    hal.start_stream(_default_frame_cb)
                else:
                    hal.start_stream(frame_cb)
                self.mark_stream_started(normalized, source="hub")
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] start_stream failed owner={normalized}"
                )
                self._state.camera.stream = StreamState.ERROR
                self.publish_error("camera", f"start_stream failed: {exc}", source="hub")
                raise

    def stop_stream(self, owner: str) -> None:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                hal.stop_stream()
                self.mark_stream_stopped(normalized, source="hub")
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] stop_stream failed owner={normalized}"
                )
                self._state.camera.stream = StreamState.ERROR
                self.publish_error("camera", f"stop_stream failed: {exc}", source="hub")
                raise

    # ──────────────────────────────────────────────────────────
    # 단일 프레임 / 배치 획득
    # ──────────────────────────────────────────────────────────

    def snap(self, owner: str):
        # 락을 설정 확인에만 사용하고 실제 노출 구간에서는 해제
        # → 노출 시간 동안 메인 스레드가 온도 폴링 등으로 블로킹되는 현상 방지
        with self._camera_lock:
            _ = validate_owner(owner)
            hal = self._require_camera_hal()

        try:
            raw = hal.snap()
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] snap failed owner={owner}")
            self.publish_error("camera", f"snap failed: {exc}", source="hub")
            raise

        with self._camera_lock:
            self._last_frame = raw
            self.publish_frame(raw, raw, source="hub")
        return raw

    def acquire_with_progress(
        self,
        owner: str,
        frame_count: int,
        on_frame: Callable[[int, int, object], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> list:
        with self._camera_lock:
            normalized = validate_owner(owner)
            hal = self._require_camera_hal()
            count = max(1, int(frame_count))

            if not self.request_exclusive_mode(normalized, mode="acquire"):
                raise HalCommandError("Cannot acquire: exclusive owner conflict")

            self.mark_acquisition_started(normalized, source="hub")
            self.publish_status(
                f"acquisition started: owner={normalized}, frames={count}", source="hub"
            )

            try:
                frames = []
                for idx in range(count):
                    if should_stop is not None and should_stop():
                        break
                    frame = hal.snap()
                    frames.append(frame)
                    if on_frame is not None:
                        on_frame(idx + 1, count, frame)

                if frames:
                    self._last_frame = frames[-1]
                    self.publish_frame(frames[-1], frames[-1], source="hub")

                self.publish_status(
                    f"acquisition finished: owner={normalized}, frames={len(frames)}",
                    source="hub",
                )
                return frames
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] acquire failed owner={normalized}, frames={count}"
                )
                self.publish_error("acquisition", f"acquire failed: {exc}", source="hub")
                raise
            finally:
                self.mark_acquisition_finished(normalized, source="hub")
                self.release_exclusive_mode(normalized, mode="acquire")

    def acquire(self, owner: str, frame_count: int) -> list:
        return self.acquire_with_progress(owner, frame_count)

    # ──────────────────────────────────────────────────────────
    # 노출 제어  [FIXED: _camera_lock 보호 추가]
    # ──────────────────────────────────────────────────────────

    def camera_set_exposure_ms(self, owner: str, ms: float) -> float:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            value = float(ms)
            try:
                hal.set_exposure_ms(value)
                actual = float(hal.get_exposure_ms())
                self.set_exposure_ms(actual, source="hub")
                self.publish_status(
                    f"camera exposure set: owner={normalized}, ms={actual:.3f}",
                    source="hub",
                )
                return actual
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_set_exposure_ms failed owner={normalized}, ms={value}"
                )
                self.publish_error("camera", f"set_exposure failed: {exc}", source="hub")
                raise

    def camera_get_exposure_ms(self, owner: str) -> float:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                actual = float(hal.get_exposure_ms())
                self.set_exposure_ms(actual, source="hub")
                return actual
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_get_exposure_ms failed owner={normalized}"
                )
                self.publish_error("camera", f"get_exposure failed: {exc}", source="hub")
                raise

    def camera_get_frame_total_s(self, owner: str) -> float:
        """프레임 총 시간(초): exposure + readout"""
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                total_s = float(hal.get_frame_total_s())
                dev_logger.debug(
                    f"[DeviceSessionHub] camera_get_frame_total_s owner={normalized} total_s={total_s}"
                )
                return total_s
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_get_frame_total_s failed owner={normalized}"
                )
                self.publish_error(
                    "camera", f"get_frame_total_s failed: {exc}", source="hub"
                )
                raise

    # ──────────────────────────────────────────────────────────
    # FPS 제어  [FIXED: _camera_lock 보호 추가]
    # ──────────────────────────────────────────────────────────

    def camera_set_fps(self, owner: str, fps: float) -> float:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            value = float(fps)
            try:
                if not hasattr(hal, "set_fps"):
                    raise HalCommandError("FPS control is not supported")
                actual = float(hal.set_fps(value))
                self.publish_status(
                    f"camera fps set: owner={normalized}, requested={value:.3f}, actual={actual:.3f}",
                    source="hub",
                )
                return actual
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_set_fps failed owner={normalized}, fps={value}"
                )
                self.publish_error("camera", f"set_fps failed: {exc}", source="hub")
                raise

    def camera_get_fps(self, owner: str) -> float:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                if not hasattr(hal, "get_fps"):
                    raise HalCommandError("FPS read is not supported")
                fps = float(hal.get_fps())
                self.publish_status(
                    f"camera fps read: owner={normalized}, fps={fps:.3f}", source="hub"
                )
                return fps
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_get_fps failed owner={normalized}"
                )
                self.publish_error("camera", f"get_fps failed: {exc}", source="hub")
                raise

    def camera_disable_fps_lock(self, owner: str) -> None:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                if not hasattr(hal, "disable_fps_lock"):
                    raise HalCommandError("FPS lock control is not supported")
                hal.disable_fps_lock()
                self.publish_status(
                    f"camera fps lock disabled: owner={normalized}", source="hub"
                )
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_disable_fps_lock failed owner={normalized}"
                )
                self.publish_error(
                    "camera", f"disable_fps_lock failed: {exc}", source="hub"
                )
                raise

    # ──────────────────────────────────────────────────────────
    # 온도 제어  [FIXED: _camera_lock 보호 추가]
    # ──────────────────────────────────────────────────────────

    def camera_set_temperature(self, owner: str, celsius: float) -> tuple:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            target = float(celsius)
            try:
                if not hasattr(hal, "set_temperature") or not hasattr(
                    hal, "get_temperature"
                ):
                    raise HalCommandError("Temperature control is not supported")
                hal.set_temperature(target)
                reading, setpoint, status = hal.get_temperature()
                self.publish_status(
                    f"camera temperature set: owner={normalized}, setpoint={float(setpoint):.2f}",
                    source="hub",
                )
                return reading, setpoint, status
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_set_temperature failed owner={normalized}, celsius={target}"
                )
                self.publish_error(
                    "camera", f"set_temperature failed: {exc}", source="hub"
                )
                raise

    def camera_get_temperature(self, owner: str) -> tuple:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                if not hasattr(hal, "get_temperature"):
                    raise HalCommandError("Temperature read is not supported")
                reading, setpoint, status = hal.get_temperature()
                self.publish_status(
                    f"camera temperature read: owner={normalized}, reading={float(reading):.2f}",
                    source="hub",
                )
                return reading, setpoint, status
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_get_temperature failed owner={normalized}"
                )
                self.publish_error(
                    "camera", f"get_temperature failed: {exc}", source="hub"
                )
                raise

    # ──────────────────────────────────────────────────────────
    # ADC 설정  [FIXED: _camera_lock 보호 추가]
    # ──────────────────────────────────────────────────────────

    def camera_set_adc_settings(self, owner: str, **kwargs) -> None:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            settings = {k: v for k, v in kwargs.items() if v is not None}
            try:
                if not hasattr(hal, "set_adc_settings"):
                    raise HalCommandError("ADC settings are not supported")
                hal.set_adc_settings(**settings)
                self.publish_status(
                    f"camera adc settings applied: owner={normalized}, keys={sorted(settings.keys())}",
                    source="hub",
                )
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_set_adc_settings failed owner={normalized}"
                )
                self.publish_error(
                    "camera", f"set_adc_settings failed: {exc}", source="hub"
                )
                raise

    def camera_get_adc_settings(self, owner: str) -> dict:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                if not hasattr(hal, "get_adc_settings"):
                    raise HalCommandError("ADC settings read is not supported")
                settings = dict(hal.get_adc_settings())
                self.publish_status(
                    f"camera adc settings read: owner={normalized}, keys={sorted(settings.keys())}",
                    source="hub",
                )
                return settings
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_get_adc_settings failed owner={normalized}"
                )
                self.publish_error(
                    "camera", f"get_adc_settings failed: {exc}", source="hub"
                )
                raise

    def camera_get_adc_candidates(self, owner: str) -> dict:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                if not hasattr(hal, "get_adc_candidates"):
                    raise HalCommandError("ADC candidates read is not supported")
                candidates = dict(hal.get_adc_candidates())
                self.publish_status(
                    f"camera adc candidates read: owner={normalized}, keys={sorted(candidates.keys())}",
                    source="hub",
                )
                return candidates
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_get_adc_candidates failed owner={normalized}"
                )
                self.publish_error(
                    "camera", f"get_adc_candidates failed: {exc}", source="hub"
                )
                raise

    # ──────────────────────────────────────────────────────────
    # ROI  [FIXED: _camera_lock 보호 추가]
    # ──────────────────────────────────────────────────────────

    def camera_set_roi(
        self,
        owner: str,
        x: int,
        y: int,
        width: int,
        height: int,
        hbin: int = 1,
        vbin: int = 1,
    ) -> None:
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                if not hasattr(hal, "set_roi"):
                    raise HalCommandError("ROI control is not supported")
                hal.set_roi(
                    int(x), int(y), int(width), int(height), int(hbin), int(vbin)
                )
                self.publish_status(
                    f"camera roi set: owner={normalized}, x={x}, y={y}, w={width}, h={height}, hbin={hbin}, vbin={vbin}",
                    source="hub",
                )
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_set_roi failed owner={normalized}"
                )
                self.publish_error("camera", f"set_roi failed: {exc}", source="hub")
                raise

    def camera_get_roi(self, owner: str):
        normalized = validate_owner(owner)
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                if not hasattr(hal, "get_roi"):
                    raise HalCommandError("ROI read is not supported")
                roi = hal.get_roi()
                self.publish_status(
                    f"camera roi read: owner={normalized}", source="hub"
                )
                return roi
            except Exception as exc:
                dev_logger.exception(
                    f"[DeviceSessionHub] camera_get_roi failed owner={normalized}"
                )
                self.publish_error("camera", f"get_roi failed: {exc}", source="hub")
                raise

    # ──────────────────────────────────────────────────────────
    # 메타 정보  [FIXED: _camera_lock 보호 추가]
    # ──────────────────────────────────────────────────────────

    def get_camera_state(self):
        return self._state.camera

    def camera_get_capabilities(self):
        with self._camera_lock:
            hal = self._require_camera_hal()
            try:
                return hal.capabilities()
            except Exception as exc:
                dev_logger.exception(
                    "[DeviceSessionHub] camera_get_capabilities failed"
                )
                self.publish_error(
                    "camera", f"get_capabilities failed: {exc}", source="hub"
                )
                raise

    def get_stream_state(self) -> StreamState:
        return self._state.camera.stream

    def get_last_frame(self):
        return self._last_frame

    # ──────────────────────────────────────────────────────────
    # 배타 모드
    # ──────────────────────────────────────────────────────────

    def request_exclusive_mode(self, owner: str, mode: str) -> bool:
        normalized = validate_owner(owner)
        if self._state.exclusive_owner not in (OWNER_NONE, normalized):
            self.publish_status(
                f"exclusive mode denied: owner={normalized}, current={self._state.exclusive_owner}",
                source="hub",
            )
            return False
        self._state.exclusive_owner = normalized
        self.publish_status(
            f"exclusive mode granted: owner={normalized}, mode={mode}", source="hub"
        )
        return True

    def release_exclusive_mode(self, owner: str, mode: str) -> None:
        normalized = validate_owner(owner)
        if self._state.exclusive_owner == normalized:
            self._state.exclusive_owner = OWNER_NONE
            self.publish_status(
                f"exclusive mode released: owner={normalized}, mode={mode}", source="hub"
            )

    # ──────────────────────────────────────────────────────────
    # ACS 모션
    # ──────────────────────────────────────────────────────────

    def attach_acs(self, acs_hal: AcsHal) -> None:
        self._acs_hal = acs_hal
        self._motion_hub.attach_acs(acs_hal)
        if hasattr(acs_hal, "positions_updated"):
            acs_hal.positions_updated.connect(self.acs_positions_updated.emit)
        if hasattr(acs_hal, "state_updated"):
            acs_hal.state_updated.connect(self.acs_states_updated.emit)
        self.mark_acs_connected()

    def connect_acs(self, ip: str, port: int) -> None:
        from core.hal.adapters import AcsMotionAdapter
        self._state.motion.acs_connection = DeviceConnectionState.CONNECTING
        self.publish_status(f"Connecting ACS: {ip}:{port}", source="hub")
        try:
            hal = AcsMotionAdapter()
            hal.connect(ip, port)
            self.attach_acs(hal)
        except Exception as exc:
            self._state.motion.acs_connection = DeviceConnectionState.ERROR
            self.publish_error("acs", f"connection failed: {exc}", source="hub")
            raise

    def connect_acs_simulator(self) -> None:
        from core.hal.adapters import AcsMotionAdapter
        self._state.motion.acs_connection = DeviceConnectionState.CONNECTING
        self.publish_status("Connecting ACS Simulator", source="hub")
        try:
            hal = AcsMotionAdapter()
            hal.connect_simulator()
            self.attach_acs(hal)
        except Exception as exc:
            self._state.motion.acs_connection = DeviceConnectionState.ERROR
            self.publish_error("acs", f"simulator connection failed: {exc}", source="hub")
            raise

    def attach_acs_controller(self, ctrl) -> None:
        """이미 연결된 AcsStageController를 SessionHub / MotionHub에 등록한다.

        AcsStagePanel이 직접 생성·연결한 컨트롤러를 사용할 때 호출한다.
        컨트롤러를 재연결하지 않고 AcsMotionAdapter로 감싸서 attach_acs()를 호출한다.
        """
        from core.hal.adapters.acs_motion_adapter import AcsMotionAdapter
        try:
            adapter = AcsMotionAdapter.from_controller(ctrl)
            self.attach_acs(adapter)  # mark_acs_connected() 포함
            dev_logger.info("[DeviceSessionHub] ACS controller attached via wrap")
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] attach_acs_controller failed")
            raise

    def detach_acs(self) -> None:
        """ACS HAL을 SessionHub / MotionHub에서 분리하고 상태를 초기화한다.

        AcsStagePanel이 disconnect 신호를 보낼 때 호출한다.
        컨트롤러 자체의 disconnect는 AcsStagePanel이 이미 처리했으므로 여기선 참조만 해제한다.
        """
        if self._acs_hal is not None:
            try:
                if hasattr(self._acs_hal, "positions_updated"):
                    self._acs_hal.positions_updated.disconnect(self.acs_positions_updated.emit)
                if hasattr(self._acs_hal, "state_updated"):
                    self._acs_hal.state_updated.disconnect(self.acs_states_updated.emit)
            except Exception:
                pass
            try:
                self._acs_hal = None
                self._motion_hub._acs_hal = None
            except Exception:
                pass
        self.mark_acs_disconnected()
        dev_logger.info("[DeviceSessionHub] ACS detached")

    def disconnect_acs(self) -> None:
        if self._acs_hal:
            try:
                if hasattr(self._acs_hal, "positions_updated"):
                    self._acs_hal.positions_updated.disconnect(self.acs_positions_updated.emit)
                if hasattr(self._acs_hal, "state_updated"):
                    self._acs_hal.state_updated.disconnect(self.acs_states_updated.emit)
            except Exception:
                pass
            try:
                self._acs_hal.disconnect()
            except Exception:
                pass
            self._acs_hal = None
            self.mark_acs_disconnected()

    def acs_enable_all(self) -> None:
        hal = self._require_acs_hal()
        try:
            hal.enable_all()
            self.publish_status("ACS enable_all requested", source="hub")
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] acs_enable_all failed")
            self.publish_error("acs", f"enable_all failed: {exc}", source="hub")
            raise

    def acs_disable_all(self) -> None:
        hal = self._require_acs_hal()
        try:
            hal.disable_all()
            self.publish_status("ACS disable_all requested", source="hub")
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] acs_disable_all failed")
            self.publish_error("acs", f"disable_all failed: {exc}", source="hub")
            raise

    def acs_stop_all(self) -> None:
        hal = self._require_acs_hal()
        try:
            hal.stop_all()
            self.publish_status("ACS stop_all requested", source="hub")
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] acs_stop_all failed")
            self.publish_error("acs", f"stop_all failed: {exc}", source="hub")
            raise

    def acs_move_to(self, axis: int, pos_mm: float) -> None:
        hal = self._require_acs_hal()
        try:
            hal.move_to(int(axis), float(pos_mm))
            # 스캔 시 포인트당 6회 호출돼 publish_status 노이즈가 큼 → DEBUG 로그만 남김.
            dev_logger.debug(
                f"[DeviceSessionHub] acs_move_to axis={axis}, pos_mm={float(pos_mm):.6f}"
            )
        except Exception as exc:
            dev_logger.exception(
                f"[DeviceSessionHub] acs_move_to failed axis={axis}, pos_mm={pos_mm}"
            )
            self.publish_error("acs", f"move_to failed: {exc}", source="hub")
            raise

    def acs_wait_in_position_all(self, timeout_ms: int = 30000) -> None:
        """6축 in-position 완료 대기. timeout 만료 시 TimeoutError 전파."""
        hal = self._require_acs_hal()
        hal.wait_in_position_all(int(timeout_ms))

    def acs_wait_for_enabled_all(self, timeout_ms: int = 2000) -> bool:
        """6축 Servo ON 확인 대기. True/False 반환."""
        hal = self._require_acs_hal()
        return bool(hal.wait_for_enabled_all(int(timeout_ms)))

    def acs_get_positions(self) -> list[float]:
        hal = self._require_acs_hal()
        try:
            return [float(v) for v in hal.get_positions()]
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] acs_get_positions failed")
            self.publish_error("acs", f"get_positions failed: {exc}", source="hub")
            raise

    def acs_get_axis_states(self) -> list[dict]:
        hal = self._require_acs_hal()
        try:
            if hasattr(hal, "get_axis_states"):
                return hal.get_axis_states()
            return []
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] acs_get_axis_states failed")
            return []

    # ──────────────────────────────────────────────────────────
    # KIMM 모션
    # ──────────────────────────────────────────────────────────

    def attach_kimm(self, kimm_hal: KimmHal) -> None:
        self._kimm_hal = kimm_hal
        self.mark_kimm_connected()

    def connect_kimm(self, ip: str, port: int) -> None:
        from core.hal.adapters import KimmMotionAdapter
        self._state.motion.kimm_connection = DeviceConnectionState.CONNECTING
        self.publish_status(f"Connecting KIMM-Z: {ip}:{port}", source="hub")
        try:
            hal = KimmMotionAdapter()
            hal.connect(ip, port)
            self.attach_kimm(hal)
        except Exception as exc:
            self._state.motion.kimm_connection = DeviceConnectionState.ERROR
            self.publish_error("kimm", f"connection failed: {exc}", source="hub")
            raise

    def disconnect_kimm(self) -> None:
        if self._kimm_hal:
            try:
                self._kimm_hal.disconnect()
            except Exception:
                pass
            self._kimm_hal = None
            self.mark_kimm_disconnected()

    def kimm_move_to_z(self, um: float, done_timeout_s: Optional[float] = None) -> None:
        hal = self._require_kimm_hal()
        try:
            if done_timeout_s is None:
                hal.move_to_z(float(um))
            else:
                # HAL 어댑터가 done_timeout_s 키워드를 지원하면 전달, 아니면 fallback.
                try:
                    hal.move_to_z(float(um), done_timeout_s=float(done_timeout_s))
                except TypeError:
                    hal.move_to_z(float(um))
            self.publish_status(
                f"KIMM move_to_z requested: um={float(um):.6f}", source="hub"
            )
        except Exception as exc:
            dev_logger.exception(
                f"[DeviceSessionHub] kimm_move_to_z failed um={um}"
            )
            self.publish_error("kimm", f"move_to_z failed: {exc}", source="hub")
            raise

    def kimm_get_z(self) -> float:
        hal = self._require_kimm_hal()
        try:
            return float(hal.get_z())
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] kimm_get_z failed")
            self.publish_error("kimm", f"get_z failed: {exc}", source="hub")
            raise

    def kimm_servo_on(self) -> None:
        hal = self._require_kimm_hal()
        try:
            hal.servo_on()
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] kimm_servo_on failed")
            self.publish_error("kimm", f"servo_on failed: {exc}", source="hub")
            raise

    def kimm_servo_off(self) -> None:
        hal = self._require_kimm_hal()
        try:
            hal.servo_off()
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] kimm_servo_off failed")
            self.publish_error("kimm", f"servo_off failed: {exc}", source="hub")
            raise

    # ──────────────────────────────────────────────────────────
    # Picomotor
    # ──────────────────────────────────────────────────────────

    def attach_pico(self, pico_hal: PicoHal) -> None:
        self._pico_hal = pico_hal
        self.mark_pico_connected()

    def connect_pico(self, *args, **kwargs) -> None:
        from core.hal.adapters import PicoMotionAdapter
        self._state.motion.pico_connection = DeviceConnectionState.CONNECTING
        self.publish_status("Connecting Picomotor", source="hub")
        try:
            hal = PicoMotionAdapter()
            hal.connect(*args, **kwargs)
            self.attach_pico(hal)
        except Exception as exc:
            self._state.motion.pico_connection = DeviceConnectionState.ERROR
            self.publish_error("pico", f"connection failed: {exc}", source="hub")
            raise

    def disconnect_pico(self) -> None:
        self.stop_pico_polling()
        if self._pico_hal:
            try:
                self._pico_hal.disconnect()
            except Exception:
                pass
            self._pico_hal = None
            self.mark_pico_disconnected()

    def pico_move_relative(self, axis: int, steps: int) -> None:
        hal = self._require_pico_hal()
        try:
            hal.move_relative(int(axis), int(steps))
            self.publish_status(
                f"Picomotor move_relative requested: axis={axis}, steps={steps}",
                source="hub",
            )
        except Exception as exc:
            dev_logger.exception(
                f"[DeviceSessionHub] pico_move_relative failed axis={axis}, steps={steps}"
            )
            self.publish_error("pico", f"move_relative failed: {exc}", source="hub")
            raise

    def pico_get_position(self, axis: int) -> int:
        hal = self._require_pico_hal()
        try:
            return int(hal.get_position(int(axis)))
        except Exception as exc:
            dev_logger.exception(
                f"[DeviceSessionHub] pico_get_position failed axis={axis}"
            )
            self.publish_error("pico", f"get_position failed: {exc}", source="hub")
            raise

    def pico_zero(self, axis: int) -> None:
        hal = self._require_pico_hal()
        try:
            hal.zero(int(axis))
            self.publish_status(
                f"Picomotor zero requested: axis={axis}", source="hub"
            )
        except Exception as exc:
            dev_logger.exception(
                f"[DeviceSessionHub] pico_zero failed axis={axis}"
            )
            self.publish_error("pico", f"zero failed: {exc}", source="hub")
            raise

    def pico_stop_all(self) -> None:
        hal = self._require_pico_hal()
        try:
            hal.stop_all()
            self.publish_status("Picomotor stop_all requested", source="hub")
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] pico_stop_all failed")
            self.publish_error("pico", f"stop_all failed: {exc}", source="hub")
            raise

    def pico_wait_motion_done(self, axis: int, timeout_ms: int) -> None:
        """위치 안정성 폴링으로 정지 대기. timeout 만료 시 TimeoutError 전파."""
        hal = self._require_pico_hal()
        hal.wait_motion_done(int(axis), int(timeout_ms))

    # ── Picomotor Hub-side 폴링 ─────────────────────────────────────────

    def start_pico_polling(self, interval_ms: int = 500) -> None:
        """Picomotor 위치 폴링을 Hub에서 시작한다. UI QTimer 대신 Hub가 단일 관리."""
        from PyQt6.QtCore import QTimer
        if self._pico_poll_timer is not None:
            return
        timer = QTimer(self)
        timer.setInterval(int(interval_ms))
        timer.timeout.connect(self._on_pico_poll_timeout)
        timer.start()
        self._pico_poll_timer = timer
        dev_logger.info(f"[DeviceSessionHub] Pico polling started interval={interval_ms}ms")

    def stop_pico_polling(self) -> None:
        """Picomotor 위치 폴링을 Hub에서 중지한다."""
        if self._pico_poll_timer is not None:
            self._pico_poll_timer.stop()
            self._pico_poll_timer = None
            dev_logger.info("[DeviceSessionHub] Pico polling stopped")

    def _on_pico_poll_timeout(self) -> None:
        hal = self._pico_hal
        if hal is None:
            self.stop_pico_polling()
            return
        try:
            positions = [hal.get_position(ax) for ax in range(1, 5)]
            self.pico_positions_updated.emit(positions)
        except Exception as exc:
            dev_logger.warning(f"[DeviceSessionHub] pico_poll failed: {exc}")

    # ── Connection Helpers ─────────────────────────────────────
    def is_acs_connected(self) -> bool:
        return self._acs_hal is not None

    @property
    def acs_controller(self):
        if self._acs_hal and hasattr(self._acs_hal, "_controller"):
            return self._acs_hal._controller
        return None

    def is_kimm_connected(self) -> bool:
        return self._kimm_hal is not None

    def is_pico_connected(self) -> bool:
        return self._pico_hal is not None

    # ── Compatibility Aliases (UI 호출 명칭 호환 레이어) ──────────────────
    def acs_connect(self, ip: str, port: int, sim: bool = False) -> None:
        if sim:
            self.connect_acs_simulator()
        else:
            self.connect_acs(ip, port)

    def acs_disconnect(self) -> None:
        self.disconnect_acs()

    def kimm_connect(self, ip: str, port: int = 5000) -> None:
        self.connect_kimm(ip, port)

    def kimm_disconnect(self) -> None:
        self.disconnect_kimm()

    def kimm_stop(self) -> None:
        self.publish_status("KIMM Z-stage stop requested (best-effort)", source="hub")
        dev_logger.info("[DeviceSessionHub] kimm_stop called (no-op by protocol)")

    def pico_connect(self, *args, **kwargs) -> None:
        self.connect_pico(*args, **kwargs)

    def pico_disconnect(self) -> None:
        self.disconnect_pico()

    # ──────────────────────────────────────────────────────────
    # MotionHub 접근
    # ──────────────────────────────────────────────────────────

    def motion(self) -> MotionHub:
        return self._motion_hub

    # ──────────────────────────────────────────────────────────
    # 상태 조회 / 직렬화
    # ──────────────────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        return self._state

    def snapshot(self) -> dict:
        return asdict(self._state)

    def set_camera_vendor(self, vendor: str) -> None:
        self._state.camera.vendor = vendor.strip()

    def set_camera_device(self, device_id: str) -> None:
        self._state.camera.device_id = device_id.strip()

    # ──────────────────────────────────────────────────────────
    # 상태 마킹 (mark_*)
    # ──────────────────────────────────────────────────────────

    def mark_camera_connected(self, source: str = "hub") -> None:
        self._state.camera.connection = CameraConnectionState.CONNECTED
        self._emit(
            SessionEventType.CAMERA_CONNECTED, source,
            device_id=self._state.camera.device_id,
        )

    def mark_camera_disconnected(self, reason: str = "", source: str = "hub") -> None:
        self._state.camera.connection = CameraConnectionState.DISCONNECTED
        self._state.camera.stream = StreamState.STOPPED
        self._state.exclusive_owner = OWNER_NONE
        self._emit(SessionEventType.CAMERA_DISCONNECTED, source, reason=reason)

    def mark_stream_started(self, owner: str, source: str = "hub") -> None:
        normalized = validate_owner(owner)
        self._state.exclusive_owner = normalized
        self._state.camera.stream = StreamState.STREAMING
        self._emit(SessionEventType.STREAM_STARTED, source, owner=normalized)

    def mark_stream_stopped(self, owner: str, source: str = "hub") -> None:
        normalized = validate_owner(owner)
        self._state.camera.stream = StreamState.STOPPED
        if self._state.exclusive_owner == normalized:
            self._state.exclusive_owner = OWNER_NONE
        self._emit(SessionEventType.STREAM_STOPPED, source, owner=normalized)

    def set_exposure_ms(self, ms: float, source: str = "hub") -> None:
        self._state.camera.exposure_ms = float(ms)
        self._emit(SessionEventType.EXPOSURE_CHANGED, source, ms=float(ms))

    def mark_acquisition_started(self, owner: str, source: str = "hub") -> None:
        normalized = validate_owner(owner)
        self._state.activity.acquisition = ActivityState.RUNNING
        self._state.exclusive_owner = normalized
        self._emit(SessionEventType.ACQUISITION_STARTED, source, owner=normalized)

    def mark_acquisition_finished(self, owner: str, source: str = "hub") -> None:
        normalized = validate_owner(owner)
        self._state.activity.acquisition = ActivityState.IDLE
        if self._state.exclusive_owner == normalized:
            self._state.exclusive_owner = OWNER_NONE
        self._emit(SessionEventType.ACQUISITION_FINISHED, source, owner=normalized)

    def mark_acs_connected(self, source: str = "hub") -> None:
        self._state.motion.acs_connection = DeviceConnectionState.CONNECTED
        self._emit(SessionEventType.ACS_CONNECTED, source)

    def mark_acs_disconnected(self, source: str = "hub") -> None:
        self._state.motion.acs_connection = DeviceConnectionState.DISCONNECTED
        self._emit(SessionEventType.ACS_DISCONNECTED, source)

    def mark_kimm_connected(self, source: str = "hub") -> None:
        self._state.motion.kimm_connection = DeviceConnectionState.CONNECTED
        self._emit(SessionEventType.KIMM_CONNECTED, source)

    def mark_kimm_disconnected(self, source: str = "hub") -> None:
        self._state.motion.kimm_connection = DeviceConnectionState.DISCONNECTED
        self._emit(SessionEventType.KIMM_DISCONNECTED, source)

    def mark_pico_connected(self, source: str = "hub") -> None:
        self._state.motion.pico_connection = DeviceConnectionState.CONNECTED
        self._emit(SessionEventType.PICO_CONNECTED, source)

    def mark_pico_disconnected(self, source: str = "hub") -> None:
        self._state.motion.pico_connection = DeviceConnectionState.DISCONNECTED
        self._emit(SessionEventType.PICO_DISCONNECTED, source)

    # ──────────────────────────────────────────────────────────
    # 이벤트 발행
    # ──────────────────────────────────────────────────────────

    def publish_error(self, scope: str, message: str, source: str = "hub") -> None:
        self._state.camera.last_error = message
        self._emit(SessionEventType.ERROR_RAISED, source, scope=scope, message=message)
        self.status_message.emit(f"[{scope}] {message}")

    def publish_status(self, text: str, source: str = "hub") -> None:
        self._emit(SessionEventType.STATUS_MESSAGE, source, text=text)
        self.status_message.emit(text)

    def publish_frame(self, rgb, raw, source: str = "hub") -> None:
        self._emit(SessionEventType.FRAME_READY, source)
        self.frame_ready.emit(rgb, raw)

    # ──────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ──────────────────────────────────────────────────────────

    # ── 카메라 온도 Hub-side 폴링 ───────────────────────────────────────

    def start_temp_polling(self, interval_ms: int = 3000) -> None:
        """카메라 온도 폴링을 Hub에서 시작한다. UI QTimer 대신 Hub가 단일 관리."""
        from PyQt6.QtCore import QTimer
        if self._temp_poll_timer is not None:
            return
        timer = QTimer(self)
        timer.setInterval(int(interval_ms))
        timer.timeout.connect(self._on_temp_poll_timeout)
        timer.start()
        self._temp_poll_timer = timer
        dev_logger.info(f"[DeviceSessionHub] Temp polling started interval={interval_ms}ms")

    def stop_temp_polling(self) -> None:
        """카메라 온도 폴링을 Hub에서 중지한다."""
        if self._temp_poll_timer is not None:
            self._temp_poll_timer.stop()
            self._temp_poll_timer = None
            dev_logger.info("[DeviceSessionHub] Temp polling stopped")

    def _on_temp_poll_timeout(self) -> None:
        with self._camera_lock:
            hal = self._camera_hal
            if hal is None or not hal.is_connected():
                self.stop_temp_polling()
                return
            # snap/acquire 중에는 락 경합 회피를 위해 건너뜀
            if not hasattr(hal, "get_temperature"):
                return
        try:
            with self._camera_lock:
                reading, setpoint, status = hal.get_temperature()
            self.camera_temperature_updated.emit(reading, setpoint, status)
        except Exception as exc:
            dev_logger.warning(f"[DeviceSessionHub] temp_poll failed: {exc}")

    def _setup_motion_hub_bindings(self) -> None:
        self._motion_hub.state_changed.connect(self._on_motion_state_changed)
        self._motion_hub.cartesian_updated.connect(self._on_motion_coords_updated)

    def _on_motion_state_changed(self, state) -> None:
        self._state.motion.state = str(state.value)
        self._emit(
            SessionEventType.MOTION_STATE_CHANGED, "motion_hub",
            state=self._state.motion.state,
        )

    def _on_motion_coords_updated(self, coords: list[float]) -> None:
        self._state.motion.current_cartesian = list(coords)
        self._emit(
            SessionEventType.MOTION_COORDS_UPDATED, "motion_hub",
            coords=list(coords),
        )

    def _emit(
        self, event_type: SessionEventType, source: str, **payload
    ) -> SessionEvent:
        event: SessionEvent = make_event(event_type, source, **payload)
        self.event_published.emit(event)
        return event

    def _require_camera_hal(self) -> CameraHal:
        """반드시 _camera_lock 보유 상태에서 호출해야 합니다."""
        hal = self._camera_hal
        if hal is None:
            raise HalNotConnectedError("Camera HAL is not attached")
        if not hal.is_connected():
            raise HalNotConnectedError("Camera is not connected")
        return hal

    def _require_acs_hal(self) -> AcsHal:
        hal = self._acs_hal
        if hal is None:
            raise HalNotConnectedError("ACS HAL is not attached")
        return hal

    def _require_kimm_hal(self) -> KimmHal:
        hal = self._kimm_hal
        if hal is None:
            raise HalNotConnectedError("KIMM HAL is not attached")
        return hal

    def _require_pico_hal(self) -> PicoHal:
        hal = self._pico_hal
        if hal is None:
            raise HalNotConnectedError("Picomotor HAL is not attached")
        return hal

    # ── 컨트롤러 직접 접근 (MotionTab용) ─────────────────────────────
    @property
    def pico_controller(self):
        return getattr(self._pico_hal, "_controller", None) if self._pico_hal else None

    @property
    def kimm_controller(self):
        return getattr(self._kimm_hal, "_controller", None) if self._kimm_hal else None

    @property
    def acs_controller(self):
        return getattr(self._acs_hal, "_controller", None) if self._acs_hal else None
