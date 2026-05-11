"""Simulated camera adapter implementing CameraHal."""

from __future__ import annotations

import numpy as np

from core.camera.simulated import SimulatedCamera, list_devices as sim_list_devices
from core.hal.camera_hal import CameraCapabilities, CameraDeviceInfo, CameraHal
from core.hal.errors import HalCommandError, HalConnectionError, HalNotConnectedError
from core.logger import cam_logger


class SimulatedCameraAdapter(CameraHal):
    def __init__(self):
        self._camera: SimulatedCamera | None = None
        self._range: tuple[float | None, float | None] = (None, None)
        self._colormap: str = "gray"

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            has_exposure=True,
            has_live=True,
            has_temperature=True,
            has_adc=True,
            has_fps_control=True,
            supports_range_control=True,
            metadata={"vendor": "simulated"},
        )

    def list_devices(self, vendor: str) -> list[CameraDeviceInfo]:
        cam_logger.debug(f"[SimulatedCameraAdapter] list_devices requested vendor={vendor}")
        devices = [
            CameraDeviceInfo(
                vendor="simulated",
                device_id=str(i),
                display_name=str(item),
            )
            for i, item in enumerate(sim_list_devices())
        ]
        cam_logger.debug(f"[SimulatedCameraAdapter] list_devices succeeded count={len(devices)}")
        return devices

    def connect(self, device_id: str) -> None:
        cam_logger.debug(f"[SimulatedCameraAdapter] connect requested device_id={device_id}")
        try:
            self._camera = SimulatedCamera()
            self._camera.connect()
            cam_logger.debug(f"[SimulatedCameraAdapter] connect succeeded device_id={device_id}")
        except Exception as exc:
            cam_logger.exception(f"[SimulatedCameraAdapter] connect failed device_id={device_id}")
            self._camera = None
            raise HalConnectionError(f"Simulated camera connect failed: {exc}", cause=exc) from exc

    def disconnect(self) -> None:
        cam_logger.debug("[SimulatedCameraAdapter] disconnect requested")
        if self._camera is None:
            cam_logger.debug("[SimulatedCameraAdapter] disconnect skipped (no camera)")
            return
        try:
            self._camera.disconnect()
            cam_logger.debug("[SimulatedCameraAdapter] disconnect succeeded")
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] disconnect failed")
            raise HalCommandError(f"Simulated camera disconnect failed: {exc}", cause=exc) from exc
        finally:
            self._camera = None

    def is_connected(self) -> bool:
        return bool(self._camera and self._camera.is_connected)

    def set_exposure_ms(self, ms: float) -> None:
        cam_logger.debug(f"[SimulatedCameraAdapter] set_exposure_ms requested ms={ms}")
        cam = self._require_connected()
        try:
            cam.set_exposure_ms(float(ms))
            cam_logger.debug(f"[SimulatedCameraAdapter] set_exposure_ms succeeded ms={ms}")
        except Exception as exc:
            cam_logger.exception(f"[SimulatedCameraAdapter] set_exposure_ms failed ms={ms}")
            raise HalCommandError(f"Simulated set exposure failed: {exc}", cause=exc) from exc

    def get_exposure_ms(self) -> float:
        cam_logger.debug("[SimulatedCameraAdapter] get_exposure_ms requested")
        cam = self._require_connected()
        try:
            ms = float(cam.get_exposure_ms())
            cam_logger.debug(f"[SimulatedCameraAdapter] get_exposure_ms succeeded ms={ms}")
            return ms
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] get_exposure_ms failed")
            raise HalCommandError(f"Simulated get exposure failed: {exc}", cause=exc) from exc

    def get_frame_total_s(self) -> float:
        """프레임 총 시간: 시뮬레이터 내부 모델(노출 + 가상 readout)을 그대로 사용"""
        try:
            cam = self._require_connected()
            if hasattr(cam, "_get_frame_total_s"):
                total_s = max(0.005, float(cam._get_frame_total_s()))
            else:
                ms = self.get_exposure_ms()
                total_s = max(0.005, ms / 1000.0)
            cam_logger.debug(f"[SimulatedCameraAdapter] get_frame_total_s succeeded s={total_s}")
            return total_s
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] get_frame_total_s failed")
            raise HalCommandError(f"Simulated get frame total time failed: {exc}", cause=exc) from exc

    def start_stream(self) -> None:
        cam_logger.debug("[SimulatedCameraAdapter] start_stream requested")
        cam = self._require_connected()
        try:
            cam.start_live(lambda _frame: None)
            cam_logger.debug("[SimulatedCameraAdapter] start_stream succeeded")
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] start_stream failed")
            raise HalCommandError(f"Simulated start stream failed: {exc}", cause=exc) from exc

    def stop_stream(self) -> None:
        cam_logger.debug("[SimulatedCameraAdapter] stop_stream requested")
        cam = self._require_connected()
        try:
            cam.stop_live()
            cam_logger.debug("[SimulatedCameraAdapter] stop_stream succeeded")
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] stop_stream failed")
            raise HalCommandError(f"Simulated stop stream failed: {exc}", cause=exc) from exc

    def snap(self) -> np.ndarray:
        cam_logger.debug("[SimulatedCameraAdapter] snap requested")
        cam = self._require_connected()
        try:
            frame = np.asarray(cam.snap())
            cam_logger.debug("[SimulatedCameraAdapter] snap succeeded")
            return frame
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] snap failed")
            raise HalCommandError(f"Simulated snap failed: {exc}", cause=exc) from exc

    def acquire(self, frame_count: int) -> list[np.ndarray]:
        cam_logger.debug(f"[SimulatedCameraAdapter] acquire requested frame_count={frame_count}")
        count = max(1, int(frame_count))
        frames: list[np.ndarray] = []
        for _ in range(count):
            frames.append(self.snap())
        cam_logger.debug(f"[SimulatedCameraAdapter] acquire succeeded frames={len(frames)}")
        return frames

    def set_range(self, vmin: float | None, vmax: float | None) -> None:
        self._range = (vmin, vmax)

    def set_colormap(self, name: str) -> None:
        self._colormap = str(name)

    def set_fps(self, fps: float) -> float:
        cam = self._require_connected()
        return float(cam.set_fps(float(fps)))

    def get_fps(self) -> float:
        cam = self._require_connected()
        return float(cam.get_fps())

    def disable_fps_lock(self) -> None:
        cam = self._require_connected()
        cam.disable_fps_lock()

    def set_temperature(self, celsius: float) -> None:
        cam = self._require_connected()
        cam.set_temperature(float(celsius))

    def get_temperature(self) -> tuple:
        cam = self._require_connected()
        return cam.get_temperature()

    def set_adc_settings(self, **kwargs) -> None:
        cam = self._require_connected()
        cam.set_adc_settings(**kwargs)

    def get_adc_settings(self) -> dict:
        cam = self._require_connected()
        return dict(cam.get_adc_settings())

    def get_adc_candidates(self) -> dict:
        caps = self.capabilities()
        return {
            "adc_quality": list(getattr(caps, "metadata", {}).get("adc_quality_options", []) or ["Low Noise", "High Capacity"]),
            "adc_speed": list(getattr(caps, "metadata", {}).get("adc_speed_options", []) or ["100kHz", "1MHz", "2MHz"]),
            "adc_analog_gain": list(getattr(caps, "metadata", {}).get("adc_gain_options", []) or ["1x", "2x", "4x"]),
            "bit_depth": list(getattr(caps, "metadata", {}).get("adc_bit_depth_options", []) or ["16bit"]),
        }

    def set_roi(self, x: int, y: int, width: int, height: int, hbin: int = 1, vbin: int = 1) -> None:
        cam = self._require_connected()
        cam.set_roi(x, y, width, height, hbin=hbin, vbin=vbin)

    def get_roi(self) -> tuple:
        cam = self._require_connected()
        return cam.get_roi()

    def _require_connected(self) -> SimulatedCamera:
        if self._camera is None or not self._camera.is_connected:
            raise HalNotConnectedError("Simulated camera is not connected")
        return self._camera
