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
            # device_id에 따라 비트 깊이 결정 (0: 16, 1: 12, 2: 8)
            bit_depth_map = {"0": 16, "1": 12, "2": 8}
            bd = bit_depth_map.get(str(device_id), 16)
            
            self._camera = SimulatedCamera(bit_depth=bd)
            self._camera.connect()
            cam_logger.debug(f"[SimulatedCameraAdapter] connect succeeded device_id={device_id} (bit_depth={bd})")
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

    def start_stream(self, frame_cb=None) -> None:
        cam_logger.info("[SimulatedCameraAdapter] grab/live start requested")
        cam = self._require_connected()
        try:
            cam.start_live(frame_cb or (lambda _frame: None))
            cam_logger.info("[SimulatedCameraAdapter] grab/live started")
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] start_stream failed")
            raise HalCommandError(f"Simulated start stream failed: {exc}", cause=exc) from exc

    def stop_stream(self) -> None:
        cam_logger.info("[SimulatedCameraAdapter] grab/live stop requested")
        cam = self._require_connected()
        try:
            cam.stop_live()
            cam_logger.info("[SimulatedCameraAdapter] grab/live stopped")
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
        cam_logger.info(f"[SimulatedCameraAdapter] acquire start frame_count={frame_count}")
        count = max(1, int(frame_count))
        frames: list[np.ndarray] = []
        for _ in range(count):
            frames.append(self.snap())
        cam_logger.info(f"[SimulatedCameraAdapter] acquire finished frames={len(frames)}")
        return frames

    def set_range(self, vmin: float | None, vmax: float | None) -> None:
        self._range = (vmin, vmax)

    def set_colormap(self, name: str) -> None:
        self._colormap = str(name)

    def set_fps(self, fps: float) -> float:
        cam_logger.debug(f"[SimulatedCameraAdapter] set_fps requested fps={fps}")
        cam = self._require_connected()
        try:
            result = float(cam.set_fps(float(fps)))
            cam_logger.debug(f"[SimulatedCameraAdapter] set_fps succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception(f"[SimulatedCameraAdapter] set_fps failed fps={fps}")
            raise HalCommandError(f"Simulated set FPS failed: {exc}", cause=exc) from exc

    def get_fps(self) -> float:
        cam_logger.debug("[SimulatedCameraAdapter] get_fps requested")
        cam = self._require_connected()
        try:
            result = float(cam.get_fps())
            cam_logger.debug(f"[SimulatedCameraAdapter] get_fps succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] get_fps failed")
            raise HalCommandError(f"Simulated get FPS failed: {exc}", cause=exc) from exc

    def disable_fps_lock(self) -> None:
        cam_logger.debug("[SimulatedCameraAdapter] disable_fps_lock requested")
        cam = self._require_connected()
        try:
            cam.disable_fps_lock()
            cam_logger.debug("[SimulatedCameraAdapter] disable_fps_lock succeeded")
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] disable_fps_lock failed")
            raise HalCommandError(f"Simulated disable FPS lock failed: {exc}", cause=exc) from exc

    def set_temperature(self, celsius: float) -> None:
        cam_logger.debug(f"[SimulatedCameraAdapter] set_temperature requested celsius={celsius}")
        cam = self._require_connected()
        try:
            cam.set_temperature(float(celsius))
            cam_logger.debug(f"[SimulatedCameraAdapter] set_temperature succeeded celsius={celsius}")
        except Exception as exc:
            cam_logger.exception(f"[SimulatedCameraAdapter] set_temperature failed celsius={celsius}")
            raise HalCommandError(f"Simulated set temperature failed: {exc}", cause=exc) from exc

    def get_temperature(self) -> tuple:
        cam_logger.debug("[SimulatedCameraAdapter] get_temperature requested")
        cam = self._require_connected()
        try:
            result = cam.get_temperature()
            cam_logger.debug(f"[SimulatedCameraAdapter] get_temperature succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] get_temperature failed")
            raise HalCommandError(f"Simulated get temperature failed: {exc}", cause=exc) from exc

    def set_adc_settings(self, **kwargs) -> None:
        cam_logger.debug(f"[SimulatedCameraAdapter] set_adc_settings requested kwargs={kwargs}")
        cam = self._require_connected()
        try:
            cam.set_adc_settings(**kwargs)
            cam_logger.debug("[SimulatedCameraAdapter] set_adc_settings succeeded")
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] set_adc_settings failed")
            raise HalCommandError(f"Simulated set ADC settings failed: {exc}", cause=exc) from exc

    def get_adc_settings(self) -> dict:
        cam_logger.debug("[SimulatedCameraAdapter] get_adc_settings requested")
        cam = self._require_connected()
        try:
            result = dict(cam.get_adc_settings())
            cam_logger.debug(f"[SimulatedCameraAdapter] get_adc_settings succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] get_adc_settings failed")
            raise HalCommandError(f"Simulated get ADC settings failed: {exc}", cause=exc) from exc

    def get_adc_candidates(self) -> dict:
        cam_logger.debug("[SimulatedCameraAdapter] get_adc_candidates requested")
        self._require_connected()  # must be connected before querying candidates
        try:
            caps = self.capabilities()
            result = {
                "adc_quality": list(getattr(caps, "metadata", {}).get("adc_quality_options", []) or ["Low Noise", "High Capacity"]),
                "adc_speed": list(getattr(caps, "metadata", {}).get("adc_speed_options", []) or ["100kHz", "1MHz", "2MHz"]),
                "adc_analog_gain": list(getattr(caps, "metadata", {}).get("adc_gain_options", []) or ["1x", "2x", "4x"]),
                "bit_depth": list(getattr(caps, "metadata", {}).get("adc_bit_depth_options", []) or ["16bit"]),
            }
            cam_logger.debug("[SimulatedCameraAdapter] get_adc_candidates succeeded")
            return result
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] get_adc_candidates failed")
            raise HalCommandError(f"Simulated get ADC candidates failed: {exc}", cause=exc) from exc

    def set_roi(self, x: int, y: int, width: int, height: int, hbin: int = 1, vbin: int = 1) -> None:
        cam_logger.debug(f"[SimulatedCameraAdapter] set_roi requested x={x} y={y} w={width} h={height} hbin={hbin} vbin={vbin}")
        cam = self._require_connected()
        try:
            cam.set_roi(x, y, width, height, hbin=hbin, vbin=vbin)
            cam_logger.debug("[SimulatedCameraAdapter] set_roi succeeded")
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] set_roi failed")
            raise HalCommandError(f"Simulated set ROI failed: {exc}", cause=exc) from exc

    def get_roi(self) -> tuple:
        cam_logger.debug("[SimulatedCameraAdapter] get_roi requested")
        cam = self._require_connected()
        try:
            result = cam.get_roi()
            cam_logger.debug(f"[SimulatedCameraAdapter] get_roi succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception("[SimulatedCameraAdapter] get_roi failed")
            raise HalCommandError(f"Simulated get ROI failed: {exc}", cause=exc) from exc

    def _require_connected(self) -> SimulatedCamera:
        if self._camera is None or not self._camera.is_connected:
            raise HalNotConnectedError("Simulated camera is not connected")
        return self._camera
