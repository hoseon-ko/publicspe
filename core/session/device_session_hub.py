"""Device session hub skeleton.

This hub is intentionally minimal in phase-1:
- No UI wiring yet
- No behavior change to existing tabs
- Provides typed state + event emission entrypoints
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.hal.camera_hal import CameraDeviceInfo, CameraHal
from core.hal.errors import HalCommandError, HalNotConnectedError
from core.hal.motion_hal import AcsHal, KimmHal, PicoHal
from core.logger import dev_logger
from core.session.ownership import OWNER_NONE, validate_owner
from core.session.session_events import SessionEvent, SessionEventType, make_event
from core.session.session_state import (
    ActivityState,
    CameraConnectionState,
    SessionState,
    StreamState,
    create_default_state,
)


class DeviceSessionHub(QObject):
    event_published = pyqtSignal(object)  # SessionEvent
    status_message = pyqtSignal(str)
    frame_ready = pyqtSignal(object, object)  # rgb, raw

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: SessionState = create_default_state()
        self._camera_hal: CameraHal | None = None
        self._camera_factories: dict[str, Callable[[], CameraHal]] = {}
        self._acs_hal: AcsHal | None = None
        self._kimm_hal: KimmHal | None = None
        self._pico_hal: PicoHal | None = None
        self._last_frame = None

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

    def connect_camera(self, device_id: str) -> None:
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
        self.publish_status(f"Connecting camera: vendor={key}, device={device_id}", source="hub")

        hal = factory()
        try:
            hal.connect(device_id)
            self._camera_hal = hal
            self.mark_camera_connected(source="hub")
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] connect_camera failed vendor={key}, device_id={device_id}")
            self._state.camera.connection = CameraConnectionState.ERROR
            self.publish_error("camera", f"connect failed: {exc}", source="hub")
            raise

    def disconnect_camera(self, reason: str = "") -> None:
        hal = self._camera_hal
        self._camera_hal = None
        if hal is not None:
            try:
                hal.disconnect()
            except Exception as exc:
                dev_logger.exception("[DeviceSessionHub] disconnect_camera failed")
                self.publish_error("camera", f"disconnect failed: {exc}", source="hub")
        self.mark_camera_disconnected(reason=reason, source="hub")

    def start_stream(self, owner: str) -> None:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()

        if self._state.camera.stream == StreamState.STREAMING:
            raise HalCommandError("Stream already running")
        if self._state.activity.acquisition == ActivityState.RUNNING and self._state.exclusive_owner != normalized:
            raise HalCommandError("Cannot start stream during acquisition by another owner")

        try:
            hal.start_stream()
            self.mark_stream_started(normalized, source="hub")
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] start_stream failed owner={normalized}")
            self._state.camera.stream = StreamState.ERROR
            self.publish_error("camera", f"start_stream failed: {exc}", source="hub")
            raise

    def stop_stream(self, owner: str) -> None:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        try:
            hal.stop_stream()
            self.mark_stream_stopped(normalized, source="hub")
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] stop_stream failed owner={normalized}")
            self._state.camera.stream = StreamState.ERROR
            self.publish_error("camera", f"stop_stream failed: {exc}", source="hub")
            raise

    def snap(self, owner: str):
        _ = validate_owner(owner)
        hal = self._require_camera_hal()
        try:
            raw = hal.snap()
            self._last_frame = raw
            self.publish_frame(raw, raw, source="hub")
            return raw
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] snap failed owner={owner}")
            self.publish_error("camera", f"snap failed: {exc}", source="hub")
            raise

    def acquire_with_progress(
        self,
        owner: str,
        frame_count: int,
        on_frame: Callable[[int, int, object], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> list:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()

        count = max(1, int(frame_count))
        if not self.request_exclusive_mode(normalized, mode="acquire"):
            raise HalCommandError("Cannot acquire: exclusive owner conflict")

        self.mark_acquisition_started(normalized, source="hub")
        self.publish_status(
            f"acquisition started: owner={normalized}, frames={count}",
            source="hub",
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
            dev_logger.exception(f"[DeviceSessionHub] acquire failed owner={normalized}, frames={count}")
            self.publish_error("acquisition", f"acquire failed: {exc}", source="hub")
            raise
        finally:
            self.mark_acquisition_finished(normalized, source="hub")
            self.release_exclusive_mode(normalized, mode="acquire")

    def acquire(self, owner: str, frame_count: int) -> list:
        return self.acquire_with_progress(owner, frame_count)

    def camera_set_exposure_ms(self, owner: str, ms: float) -> float:
        normalized = validate_owner(owner)
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
            dev_logger.exception(f"[DeviceSessionHub] camera_set_exposure_ms failed owner={normalized}, ms={value}")
            self.publish_error("camera", f"set_exposure failed: {exc}", source="hub")
            raise

    def camera_get_exposure_ms(self, owner: str) -> float:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        try:
            actual = float(hal.get_exposure_ms())
            self.set_exposure_ms(actual, source="hub")
            return actual
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_get_exposure_ms failed owner={normalized}")
            self.publish_error("camera", f"get_exposure failed: {exc}", source="hub")
            raise

    def camera_get_frame_total_s(self, owner: str) -> float:
        """프레임 총 시간(초): exposure + readout"""
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        try:
            total_s = float(hal.get_frame_total_s())
            dev_logger.debug(f"[DeviceSessionHub] camera_get_frame_total_s owner={normalized} total_s={total_s}")
            return total_s
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_get_frame_total_s failed owner={normalized}")
            self.publish_error("camera", f"get_frame_total_s failed: {exc}", source="hub")
            raise

    def camera_set_fps(self, owner: str, fps: float) -> float:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        value = float(fps)
        try:
            if not hasattr(hal, "set_fps"):
                raise HalCommandError("FPS control is not supported")
            actual = float(hal.set_fps(value))
            self.publish_status(
                f"camera fps set: owner={normalized}, fps={actual:.3f}",
                source="hub",
            )
            return actual
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_set_fps failed owner={normalized}, fps={value}")
            self.publish_error("camera", f"set_fps failed: {exc}", source="hub")
            raise

    def camera_get_fps(self, owner: str) -> float:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        try:
            if not hasattr(hal, "get_fps"):
                raise HalCommandError("FPS read is not supported")
            fps = float(hal.get_fps())
            self.publish_status(
                f"camera fps read: owner={normalized}, fps={fps:.3f}",
                source="hub",
            )
            return fps
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_get_fps failed owner={normalized}")
            self.publish_error("camera", f"get_fps failed: {exc}", source="hub")
            raise

    def camera_disable_fps_lock(self, owner: str) -> None:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        try:
            if not hasattr(hal, "disable_fps_lock"):
                raise HalCommandError("FPS lock control is not supported")
            hal.disable_fps_lock()
            self.publish_status(
                f"camera fps lock disabled: owner={normalized}",
                source="hub",
            )
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_disable_fps_lock failed owner={normalized}")
            self.publish_error("camera", f"disable_fps_lock failed: {exc}", source="hub")
            raise

    def camera_set_temperature(self, owner: str, celsius: float) -> tuple:
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        target = float(celsius)
        try:
            if not hasattr(hal, "set_temperature") or not hasattr(hal, "get_temperature"):
                raise HalCommandError("Temperature control is not supported")
            hal.set_temperature(target)
            reading, setpoint, status = hal.get_temperature()
            self.publish_status(
                f"camera temperature set: owner={normalized}, setpoint={float(setpoint):.2f}",
                source="hub",
            )
            return reading, setpoint, status
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_set_temperature failed owner={normalized}, celsius={target}")
            self.publish_error("camera", f"set_temperature failed: {exc}", source="hub")
            raise

    def camera_get_temperature(self, owner: str) -> tuple:
        normalized = validate_owner(owner)
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
            dev_logger.exception(f"[DeviceSessionHub] camera_get_temperature failed owner={normalized}")
            self.publish_error("camera", f"get_temperature failed: {exc}", source="hub")
            raise

    def camera_set_adc_settings(self, owner: str, **kwargs) -> None:
        normalized = validate_owner(owner)
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
            dev_logger.exception(f"[DeviceSessionHub] camera_set_adc_settings failed owner={normalized}")
            self.publish_error("camera", f"set_adc_settings failed: {exc}", source="hub")
            raise

    def camera_get_adc_settings(self, owner: str) -> dict:
        normalized = validate_owner(owner)
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
            dev_logger.exception(f"[DeviceSessionHub] camera_get_adc_settings failed owner={normalized}")
            self.publish_error("camera", f"get_adc_settings failed: {exc}", source="hub")
            raise

    def camera_get_adc_candidates(self, owner: str) -> dict:
        normalized = validate_owner(owner)
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
            dev_logger.exception(f"[DeviceSessionHub] camera_get_adc_candidates failed owner={normalized}")
            self.publish_error("camera", f"get_adc_candidates failed: {exc}", source="hub")
            raise

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
        hal = self._require_camera_hal()
        try:
            if not hasattr(hal, "set_roi"):
                raise HalCommandError("ROI control is not supported")
            hal.set_roi(int(x), int(y), int(width), int(height), int(hbin), int(vbin))
            self.publish_status(
                f"camera roi set: owner={normalized}, x={int(x)}, y={int(y)}, w={int(width)}, h={int(height)}, hbin={int(hbin)}, vbin={int(vbin)}",
                source="hub",
            )
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_set_roi failed owner={normalized}")
            self.publish_error("camera", f"set_roi failed: {exc}", source="hub")
            raise

    def camera_get_roi(self, owner: str):
        normalized = validate_owner(owner)
        hal = self._require_camera_hal()
        try:
            if not hasattr(hal, "get_roi"):
                raise HalCommandError("ROI read is not supported")
            roi = hal.get_roi()
            self.publish_status(f"camera roi read: owner={normalized}", source="hub")
            return roi
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] camera_get_roi failed owner={normalized}")
            self.publish_error("camera", f"get_roi failed: {exc}", source="hub")
            raise

    def get_camera_state(self):
        return self._state.camera

    def camera_get_capabilities(self):
        hal = self._require_camera_hal()
        try:
            return hal.capabilities()
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] camera_get_capabilities failed")
            self.publish_error("camera", f"get_capabilities failed: {exc}", source="hub")
            raise

    def get_stream_state(self) -> StreamState:
        return self._state.camera.stream

    def get_last_frame(self):
        return self._last_frame

    def request_exclusive_mode(self, owner: str, mode: str) -> bool:
        normalized = validate_owner(owner)
        if self._state.exclusive_owner not in (OWNER_NONE, normalized):
            self.publish_status(
                f"exclusive mode denied: owner={normalized}, current={self._state.exclusive_owner}",
                source="hub",
            )
            return False

        self._state.exclusive_owner = normalized
        self.publish_status(f"exclusive mode granted: owner={normalized}, mode={mode}", source="hub")
        return True

    def release_exclusive_mode(self, owner: str, mode: str) -> None:
        normalized = validate_owner(owner)
        if self._state.exclusive_owner == normalized:
            self._state.exclusive_owner = OWNER_NONE
            self.publish_status(f"exclusive mode released: owner={normalized}, mode={mode}", source="hub")

    def attach_acs(self, acs_hal: AcsHal) -> None:
        self._acs_hal = acs_hal

    def attach_kimm(self, kimm_hal: KimmHal) -> None:
        self._kimm_hal = kimm_hal

    def attach_pico(self, pico_hal: PicoHal) -> None:
        self._pico_hal = pico_hal

    # Motion HAL APIs (phase-1 minimal)
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
            self.publish_status(
                f"ACS move_to requested: axis={int(axis)}, pos_mm={float(pos_mm):.6f}",
                source="hub",
            )
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] acs_move_to failed axis={int(axis)}, pos_mm={float(pos_mm)}")
            self.publish_error("acs", f"move_to failed: {exc}", source="hub")
            raise

    def acs_get_positions(self) -> list[float]:
        hal = self._require_acs_hal()
        try:
            return [float(v) for v in hal.get_positions()]
        except Exception as exc:
            dev_logger.exception("[DeviceSessionHub] acs_get_positions failed")
            self.publish_error("acs", f"get_positions failed: {exc}", source="hub")
            raise

    def kimm_move_to_z(self, um: float) -> None:
        hal = self._require_kimm_hal()
        try:
            hal.move_to_z(float(um))
            self.publish_status(f"KIMM move_to_z requested: um={float(um):.6f}", source="hub")
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] kimm_move_to_z failed um={float(um)}")
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

    def pico_move_relative(self, axis: int, steps: int) -> None:
        hal = self._require_pico_hal()
        try:
            hal.move_relative(int(axis), int(steps))
            self.publish_status(
                f"Picomotor move_relative requested: axis={int(axis)}, steps={int(steps)}",
                source="hub",
            )
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] pico_move_relative failed axis={int(axis)}, steps={int(steps)}")
            self.publish_error("pico", f"move_relative failed: {exc}", source="hub")
            raise

    def pico_get_position(self, axis: int) -> int:
        hal = self._require_pico_hal()
        try:
            return int(hal.get_position(int(axis)))
        except Exception as exc:
            dev_logger.exception(f"[DeviceSessionHub] pico_get_position failed axis={int(axis)}")
            self.publish_error("pico", f"get_position failed: {exc}", source="hub")
            raise

    @property
    def state(self) -> SessionState:
        return self._state

    def snapshot(self) -> dict:
        return asdict(self._state)

    def set_camera_vendor(self, vendor: str) -> None:
        self._state.camera.vendor = vendor.strip()

    def set_camera_device(self, device_id: str) -> None:
        self._state.camera.device_id = device_id.strip()

    def mark_camera_connected(self, source: str = "hub") -> None:
        self._state.camera.connection = CameraConnectionState.CONNECTED
        self._emit(SessionEventType.CAMERA_CONNECTED, source, device_id=self._state.camera.device_id)

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

    def _emit(self, event_type: SessionEventType, source: str, **payload) -> SessionEvent:
        event: SessionEvent = make_event(event_type, source, **payload)
        self.event_published.emit(event)
        return event

    def _require_camera_hal(self) -> CameraHal:
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
