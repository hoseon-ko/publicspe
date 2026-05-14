"""Picam camera adapter implementing CameraHal."""

from __future__ import annotations

import numpy as np

from core.camera.picamp import PicamCamera, list_devices as picam_list_devices
from core.hal.camera_hal import CameraCapabilities, CameraDeviceInfo, CameraHal
from core.hal.errors import HalCommandError, HalConnectionError, HalNotConnectedError
from core.logger import cam_logger


class PicamCameraAdapter(CameraHal):
    def __init__(self):
        self._camera: PicamCamera | None = None
        self._serial_hint: str | None = None
        self._range: tuple[float | None, float | None] = (None, None)
        self._colormap: str = "gray"

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            has_exposure=True,
            has_live=True,
            has_temperature=True,
            has_adc=True,
            supports_range_control=True,
            metadata={"vendor": "picam"},
        )

    def list_devices(self, vendor: str) -> list[CameraDeviceInfo]:
        cam_logger.debug(f"[PicamCameraAdapter] list_devices requested vendor={vendor}")
        devices = picam_list_devices()
        results: list[CameraDeviceInfo] = []
        for i, item in enumerate(devices):
            text = str(item)
            results.append(
                CameraDeviceInfo(
                    vendor="picam",
                    device_id=text if text else str(i),
                    display_name=text if text else f"Picam {i}",
                    serial=text,
                )
            )
        cam_logger.debug(f"[PicamCameraAdapter] list_devices succeeded count={len(results)}")
        return results

    def connect(self, device_id: str) -> None:
        cam_logger.debug(f"[PicamCameraAdapter] connect requested device_id={device_id}")
        try:
            self._serial_hint = device_id.strip() or None
            self._camera = PicamCamera(serial_number=self._serial_hint)
            self._camera.connect()
            cam_logger.debug(f"[PicamCameraAdapter] connect succeeded device_id={device_id}")
        except Exception as exc:
            cam_logger.exception(f"[PicamCameraAdapter] connect failed device_id={device_id}")
            self._camera = None
            raise HalConnectionError(f"Picam connect failed: {exc}", cause=exc) from exc

    def disconnect(self) -> None:
        cam_logger.debug("[PicamCameraAdapter] disconnect requested")
        if self._camera is None:
            cam_logger.debug("[PicamCameraAdapter] disconnect skipped (no camera)")
            return
        try:
            self._camera.disconnect()
            cam_logger.debug("[PicamCameraAdapter] disconnect succeeded")
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] disconnect failed")
            raise HalCommandError(f"Picam disconnect failed: {exc}", cause=exc) from exc
        finally:
            self._camera = None

    def is_connected(self) -> bool:
        return bool(self._camera and self._camera.is_connected)

    def set_exposure_ms(self, ms: float) -> None:
        cam_logger.debug(f"[PicamCameraAdapter] set_exposure_ms requested ms={ms}")
        cam = self._require_connected()
        try:
            cam.set_exposure_ms(float(ms))
            cam_logger.debug(f"[PicamCameraAdapter] set_exposure_ms succeeded ms={ms}")
        except Exception as exc:
            cam_logger.exception(f"[PicamCameraAdapter] set_exposure_ms failed ms={ms}")
            raise HalCommandError(f"Picam set exposure failed: {exc}", cause=exc) from exc

    def get_exposure_ms(self) -> float:
        cam_logger.debug("[PicamCameraAdapter] get_exposure_ms requested")
        cam = self._require_connected()
        try:
            ms = float(cam.get_exposure_ms())
            cam_logger.debug(f"[PicamCameraAdapter] get_exposure_ms succeeded ms={ms}")
            return ms
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] get_exposure_ms failed")
            raise HalCommandError(f"Picam get exposure failed: {exc}", cause=exc) from exc

    def get_frame_total_s(self) -> float:
        """프레임 총 시간: exposure + readout (PiCam SDK에서 계산)"""
        cam_logger.debug("[PicamCameraAdapter] get_frame_total_s requested")
        cam = self._require_connected()
        try:
            # Use public API if available; private _get_frame_total_s is an implementation detail
            fn = getattr(cam, "get_frame_total_s", None) or getattr(cam, "_get_frame_total_s", None)
            if fn is not None:
                total_s = float(fn())
            else:
                # Fallback: estimate from exposure only
                total_s = self.get_exposure_ms() / 1000.0
            cam_logger.debug(f"[PicamCameraAdapter] get_frame_total_s succeeded s={total_s}")
            return total_s
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] get_frame_total_s failed")
            raise HalCommandError(f"Picam get frame total time failed: {exc}", cause=exc) from exc

    def start_stream(self, frame_cb=None) -> None:
        cam_logger.info("[PicamCameraAdapter] grab/live start requested")
        cam = self._require_connected()
        try:
            cam.start_live(frame_cb or (lambda _frame: None))
            cam_logger.info("[PicamCameraAdapter] grab/live started")
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] start_stream failed")
            raise HalCommandError(f"Picam start stream failed: {exc}", cause=exc) from exc

    def stop_stream(self) -> None:
        cam_logger.info("[PicamCameraAdapter] grab/live stop requested")
        cam = self._require_connected()
        try:
            cam.stop_live()
            cam_logger.info("[PicamCameraAdapter] grab/live stopped")
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] stop_stream failed")
            raise HalCommandError(f"Picam stop stream failed: {exc}", cause=exc) from exc

    def snap(self) -> np.ndarray:
        cam_logger.debug("[PicamCameraAdapter] snap requested")
        cam = self._require_connected()
        try:
            frame = np.asarray(cam.snap())
            cam_logger.debug("[PicamCameraAdapter] snap succeeded")
            return frame
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] snap failed")
            raise HalCommandError(f"Picam snap failed: {exc}", cause=exc) from exc

    def acquire(self, frame_count: int) -> list[np.ndarray]:
        cam_logger.info(f"[PicamCameraAdapter] acquire start frame_count={frame_count}")
        count = max(1, int(frame_count))
        frames: list[np.ndarray] = []
        for _ in range(count):
            frames.append(self.snap())
        cam_logger.info(f"[PicamCameraAdapter] acquire finished frames={len(frames)}")
        return frames

    def set_range(self, vmin: float | None, vmax: float | None) -> None:
        self._range = (vmin, vmax)

    def set_colormap(self, name: str) -> None:
        self._colormap = str(name)

    def set_temperature(self, celsius: float) -> None:
        cam_logger.debug(f"[PicamCameraAdapter] set_temperature requested celsius={celsius}")
        cam = self._require_connected()
        try:
            cam.set_temperature(float(celsius))
            cam_logger.debug(f"[PicamCameraAdapter] set_temperature succeeded celsius={celsius}")
        except Exception as exc:
            cam_logger.exception(f"[PicamCameraAdapter] set_temperature failed celsius={celsius}")
            raise HalCommandError(f"Picam set temperature failed: {exc}", cause=exc) from exc

    def get_temperature(self) -> tuple:
        cam_logger.debug("[PicamCameraAdapter] get_temperature requested")
        cam = self._require_connected()
        try:
            result = cam.get_temperature()
            cam_logger.debug(f"[PicamCameraAdapter] get_temperature succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] get_temperature failed")
            raise HalCommandError(f"Picam get temperature failed: {exc}", cause=exc) from exc

    def set_adc_settings(self, **kwargs) -> None:
        cam_logger.debug(f"[PicamCameraAdapter] set_adc_settings requested kwargs={kwargs}")
        cam = self._require_connected()
        try:
            cam.set_adc_settings(**kwargs)
            cam_logger.debug("[PicamCameraAdapter] set_adc_settings succeeded")
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] set_adc_settings failed")
            raise HalCommandError(f"Picam set ADC settings failed: {exc}", cause=exc) from exc

    def get_adc_settings(self) -> dict:
        cam_logger.debug("[PicamCameraAdapter] get_adc_settings requested")
        cam = self._require_connected()
        try:
            result = dict(cam.get_adc_settings())
            cam_logger.debug(f"[PicamCameraAdapter] get_adc_settings succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] get_adc_settings failed")
            raise HalCommandError(f"Picam get ADC settings failed: {exc}", cause=exc) from exc

    def get_adc_candidates(self) -> dict:
        cam_logger.debug("[PicamCameraAdapter] get_adc_candidates requested")
        cam = self._require_connected()
        try:
            result = dict(cam.get_adc_candidates())
            cam_logger.debug("[PicamCameraAdapter] get_adc_candidates succeeded")
            return result
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] get_adc_candidates failed")
            raise HalCommandError(f"Picam get ADC candidates failed: {exc}", cause=exc) from exc

    def set_roi(self, x: int, y: int, width: int, height: int, hbin: int = 1, vbin: int = 1) -> None:
        cam_logger.debug(f"[PicamCameraAdapter] set_roi requested x={x} y={y} w={width} h={height} hbin={hbin} vbin={vbin}")
        cam = self._require_connected()
        try:
            cam.set_roi(x, y, width, height, hbin=hbin, vbin=vbin)
            cam_logger.debug("[PicamCameraAdapter] set_roi succeeded")
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] set_roi failed")
            raise HalCommandError(f"Picam set ROI failed: {exc}", cause=exc) from exc

    def get_roi(self) -> tuple | None:
        cam_logger.debug("[PicamCameraAdapter] get_roi requested")
        cam = self._require_connected()
        try:
            result = cam.get_roi()
            cam_logger.debug(f"[PicamCameraAdapter] get_roi succeeded result={result}")
            return result
        except Exception as exc:
            cam_logger.exception("[PicamCameraAdapter] get_roi failed")
            raise HalCommandError(f"Picam get ROI failed: {exc}", cause=exc) from exc

    def _require_connected(self) -> PicamCamera:
        if self._camera is None or not self._camera.is_connected:
            raise HalNotConnectedError("Picam camera is not connected")
        return self._camera
