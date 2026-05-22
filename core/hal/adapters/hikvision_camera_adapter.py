"""Hikvision camera adapter implementing CameraHal."""

from __future__ import annotations

import re

import numpy as np

from core.camera.hikvision import HikvisionCamera, list_devices as hik_list_devices
from core.hal.camera_hal import CameraCapabilities, CameraDeviceInfo, CameraHal
from core.hal.errors import HalCommandError, HalConnectionError, HalNotConnectedError
from core.logger import cam_logger


class HikvisionCameraAdapter(CameraHal):
    def __init__(self):
        self._camera: HikvisionCamera | None = None
        self._device_index: int = 0
        self._range: tuple[float | None, float | None] = (None, None)
        self._colormap: str = "gray"

    def capabilities(self) -> CameraCapabilities:
        exp_range = (0.001, 1_000_000.0)
        fps_range = (0.1, 1000.0)
        if self._camera is not None:
            base_caps = self._camera.capabilities
            exp_range = base_caps.exposure_range_ms
            fps_range = base_caps.fps_range
        return CameraCapabilities(
            has_exposure=True,
            has_live=True,
            has_fps_control=True,
            has_binarize=True,
            supports_range_control=True,
            exposure_range_ms=exp_range,
            fps_range=fps_range,
            metadata={"vendor": "hikvision"},
        )

    def list_devices(self, vendor: str) -> list[CameraDeviceInfo]:
        cam_logger.debug(f"[HikvisionCameraAdapter] list_devices requested vendor={vendor}")
        devices = hik_list_devices()
        results: list[CameraDeviceInfo] = []
        for i, item in enumerate(devices):
            display_name = str(item)
            results.append(
                CameraDeviceInfo(
                    vendor="hikvision",
                    device_id=str(i),
                    display_name=display_name,
                )
            )
        cam_logger.debug(f"[HikvisionCameraAdapter] list_devices succeeded count={len(results)}")
        return results

    def connect(self, device_id: str) -> None:
        cam_logger.debug(f"[HikvisionCameraAdapter] connect requested device_id={device_id}")
        try:
            self._device_index = self._parse_device_index(device_id)
            self._camera = HikvisionCamera(device_index=self._device_index)
            self._camera.connect()
            cam_logger.debug(f"[HikvisionCameraAdapter] connect succeeded device_id={device_id}")
        except Exception as exc:
            cam_logger.exception(f"[HikvisionCameraAdapter] connect failed device_id={device_id}")
            self._camera = None
            raise HalConnectionError(f"Hikvision connect failed: {exc}", cause=exc) from exc

    def disconnect(self) -> None:
        cam_logger.debug("[HikvisionCameraAdapter] disconnect requested")
        if self._camera is None:
            cam_logger.debug("[HikvisionCameraAdapter] disconnect skipped (no camera)")
            return
        try:
            self._camera.disconnect()
            cam_logger.debug("[HikvisionCameraAdapter] disconnect succeeded")
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] disconnect failed")
            raise HalCommandError(f"Hikvision disconnect failed: {exc}", cause=exc) from exc
        finally:
            self._camera = None

    def is_connected(self) -> bool:
        return bool(self._camera and self._camera.is_connected)

    def set_exposure_ms(self, ms: float) -> None:
        cam_logger.debug(f"[HikvisionCameraAdapter] set_exposure_ms requested ms={ms}")
        cam = self._require_connected()
        try:
            cam.set_exposure_ms(float(ms))
            cam_logger.debug(f"[HikvisionCameraAdapter] set_exposure_ms succeeded ms={ms}")
        except Exception as exc:
            cam_logger.exception(f"[HikvisionCameraAdapter] set_exposure_ms failed ms={ms}")
            raise HalCommandError(f"Hikvision set exposure failed: {exc}", cause=exc) from exc

    def get_exposure_ms(self) -> float:
        cam_logger.debug("[HikvisionCameraAdapter] get_exposure_ms requested")
        cam = self._require_connected()
        try:
            ms = float(cam.get_exposure_ms())
            cam_logger.debug(f"[HikvisionCameraAdapter] get_exposure_ms succeeded ms={ms}")
            return ms
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] get_exposure_ms failed")
            raise HalCommandError(f"Hikvision get exposure failed: {exc}", cause=exc) from exc

    def get_frame_total_s(self) -> float:
        """프레임 총 시간(초) = 라이브 프레임 주기 추정값.

        HIKVISION은 FPS 제어를 지원하는 카메라이므로 프레임 주기는 **1/FPS** 가 기준이다.
        MVS SDK ResultingFrameRate 는 exposure + readout 제약을 이미 반영한
        '실제 달성 가능 FPS' 라서 1/fps 가 곧 (노출+리드아웃) 을 포함한 프레임 주기다.
        따라서 노출과 max() 로 섞지 않고 FPS 를 그대로 따른다.
        FPS 를 읽지 못할 때만 노출 시간으로 폴백한다 (FPS 미지원 카메라의 Readout+Exposure 대체).
        """
        try:
            fps = 0.0
            try:
                fps = self.get_fps()
            except Exception:
                cam_logger.debug("[HikvisionCameraAdapter] get_frame_total_s: FPS read failed, fallback to exposure")
            if fps > 0:
                total_s = 1.0 / fps
            else:
                total_s = max(0.005, self.get_exposure_ms() / 1000.0)
            cam_logger.debug(f"[HikvisionCameraAdapter] get_frame_total_s succeeded s={total_s} fps={fps}")
            return total_s
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] get_frame_total_s failed")
            raise HalCommandError(f"Hikvision get frame total time failed: {exc}", cause=exc) from exc

    def start_stream(self, frame_cb=None) -> None:
        cam_logger.info("[HikvisionCameraAdapter] grab/live start requested")
        cam = self._require_connected()
        try:
            cam.start_live(frame_cb or (lambda _frame: None))
            cam_logger.info("[HikvisionCameraAdapter] grab/live started")
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] start_stream failed")
            raise HalCommandError(f"Hikvision start stream failed: {exc}", cause=exc) from exc

    def stop_stream(self) -> None:
        cam_logger.info("[HikvisionCameraAdapter] grab/live stop requested")
        cam = self._require_connected()
        try:
            cam.stop_live()
            cam_logger.info("[HikvisionCameraAdapter] grab/live stopped")
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] stop_stream failed")
            raise HalCommandError(f"Hikvision stop stream failed: {exc}", cause=exc) from exc

    def snap(self) -> np.ndarray:
        cam_logger.debug("[HikvisionCameraAdapter] snap requested")
        cam = self._require_connected()
        try:
            frame = np.asarray(cam.snap())
            cam_logger.debug("[HikvisionCameraAdapter] snap succeeded")
            return frame
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] snap failed")
            raise HalCommandError(f"Hikvision snap failed: {exc}", cause=exc) from exc

    def acquire(self, frame_count: int) -> list[np.ndarray]:
        cam_logger.info(f"[HikvisionCameraAdapter] acquire start frame_count={frame_count}")
        count = max(1, int(frame_count))
        frames: list[np.ndarray] = []
        for _ in range(count):
            frames.append(self.snap())
        cam_logger.info(f"[HikvisionCameraAdapter] acquire finished frames={len(frames)}")
        return frames

    def set_range(self, vmin: float | None, vmax: float | None) -> None:
        self._range = (vmin, vmax)

    def set_colormap(self, name: str) -> None:
        self._colormap = str(name)

    def set_fps(self, fps: float) -> float:
        cam = self._require_connected()
        try:
            return float(cam.set_fps(float(fps)))
        except Exception as exc:
            cam_logger.exception(f"[HikvisionCameraAdapter] set_fps failed fps={fps}")
            raise HalCommandError(f"Hikvision set fps failed: {exc}", cause=exc) from exc

    def get_fps(self) -> float:
        cam = self._require_connected()
        try:
            return float(cam.get_fps())
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] get_fps failed")
            raise HalCommandError(f"Hikvision get fps failed: {exc}", cause=exc) from exc

    def disable_fps_lock(self) -> None:
        cam = self._require_connected()
        try:
            cam.disable_fps_lock()
        except Exception as exc:
            cam_logger.exception("[HikvisionCameraAdapter] disable_fps_lock failed")
            raise HalCommandError(f"Hikvision disable fps lock failed: {exc}", cause=exc) from exc

    def _require_connected(self) -> HikvisionCamera:
        if self._camera is None or not self._camera.is_connected:
            raise HalNotConnectedError("Hikvision camera is not connected")
        return self._camera

    @staticmethod
    def _parse_device_index(device_id: str) -> int:
        text = str(device_id).strip()
        if text.isdigit():
            return int(text)
        match = re.search(r"(\d+)", text)
        if match:
            return int(match.group(1))
        return 0
