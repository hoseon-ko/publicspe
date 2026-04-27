import struct
import time
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from xml.sax.saxutils import escape

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

_PICAM_OK = False
_PICAM_IMPORT_ERROR: Optional[str] = None

try:
    from pylablib.devices import PrincetonInstruments
    _PICAM_OK = True
except Exception as e:
    _PICAM_IMPORT_ERROR = str(e)

from core.camera.base import BaseCamera, CameraCapabilities, NotSupportedError


# 모델별로 속성명이 달라질 수 있어 alias 목록을 둔다.
ADC_ATTR_ALIASES = {
    "adc_quality": ["Adc Quality", "ADC Quality"],
    "adc_speed": ["Adc Speed", "ADC Speed"],
    "adc_analog_gain": ["Adc Analog Gain", "ADC Analog Gain"],
    "bit_depth": ["Bit Depth", "Adc Bit Depth", "ADC Bit Depth"],
    "readout_ports_used": ["Readout Ports Used", "Readout Port Count"],
}

# SPE 3.0 datatype codes (same as spe_loader/spe2py)
_SPE_DTYPE_CODE: Dict = {
    "float32": 0,
    "int32":   1,
    "int16":   2,
    "uint16":  3,
    "uint32":  8,
}

_SPE_PIXEL_FORMAT: Dict[str, str] = {
    "float32": "MonochromeFloating32",
    "uint16": "MonochromeUnsigned16",
    "uint32": "MonochromeUnsigned32",
}

__all__ = [
    # 클래스
    "PicamCameraWrapper",
    "PicamCamera",
    # 카메라 연결
    "is_available",
    "list_devices",
    "list_cameras",
    "open_camera",
    "close_camera",
    # 노출 / ROI
    "get_exposure_ms",
    "set_exposure_ms",
    "get_roi",
    "set_roi",
    # ADC
    "get_adc_candidate_map",
    "apply_adc_settings",
    # 온도
    "read_temperature_block",
    "set_temperature_setpoint",
    "wait_temperature_lock",
    "apply_temperature_settings",
    # 이미지 획득
    "snap_image",
    "acquire_images",
    # 저장
    "save_as_spe",
    # 블록 설정
    "apply_camera_block",
]


# ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

def _get_attr_safe(cam, name: str, default: Any = None) -> Any:
    try:
        return cam.get_attribute_value(name, error_on_missing=False, default=default)
    except Exception:
        return default


def _set_attr_by_aliases(cam, aliases: List[str], value: Any) -> Tuple[str, Any]:
    for name in aliases:
        try:
            cam.set_attribute_value(name, value, error_on_missing=False)
            current = _get_attr_safe(cam, name, default=None)
            if current is not None:
                return name, current
        except Exception:
            pass
    return "", None


def _xml_tag(tag: str, value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text == "":
        return ""
    return f"<{tag}>{escape(text)}</{tag}>"


def _parse_first_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


# ── PicamCameraWrapper ─────────────────────────────────────────────────────

class PicamCameraWrapper:
    """
    pylablib PicamCamera 위에 사용자 편의 기능을 더한 래퍼.

    기본 라이브러리 파일을 수정하지 않고,
    모델별 속성명 차이와 enum 조회/안전 설정 로직을 캡슐화한다.
    """

    def __init__(self, cam=None, serial_number: Optional[str] = None):
        self.cam = cam
        self.serial_number = serial_number

    def open(self):
        if self.cam is None:
            self.cam = PrincetonInstruments.PicamCamera(serial_number=self.serial_number)
        return self.cam

    def close(self):
        if self.cam is not None:
            try:
                self.cam.close()
            finally:
                self.cam = None

    def get_attr_safe(self, name: str, default: Any = None) -> Any:
        if self.cam is None:
            raise RuntimeError("Camera is not opened")
        return _get_attr_safe(self.cam, name, default=default)

    def set_attr_safe(self, aliases: List[str], value: Any) -> Tuple[str, Any]:
        if self.cam is None:
            raise RuntimeError("Camera is not opened")
        return _set_attr_by_aliases(self.cam, aliases, value)

    def resolve_attr_name(self, aliases: List[str]) -> str:
        if self.cam is None:
            raise RuntimeError("Camera is not opened")
        for name in aliases:
            attr = self.cam.get_attribute(name, error_on_missing=False)
            if attr is not None and getattr(attr, "exists", True):
                return name
        return ""

    def get_enum_candidates(self, aliases: List[str]) -> List[Any]:
        """해당 속성이 enum이면 가능한 값 목록을 반환한다."""
        if self.cam is None:
            raise RuntimeError("Camera is not opened")
        name = self.resolve_attr_name(aliases)
        if not name:
            return []
        attr = self.cam.get_attribute(name, error_on_missing=False)
        if attr is None:
            return []
        try:
            attr.update_limits()
        except Exception:
            pass
        values = getattr(attr, "values", None)
        if values:
            return list(values)
        ivalues = getattr(attr, "ivalues", None)
        if ivalues:
            return list(ivalues)
        return []

    def get_adc_candidate_map(self) -> Dict[str, Dict[str, Any]]:
        """
        ADC 관련 속성의 실제 이름과 가능한 후보값을 반환한다.
        반환 예시:
          {
            "adc_speed": {
              "attribute": "Adc Speed",
              "candidates": ["1 MHz", "4 MHz"]
            }
          }
        """
        if self.cam is None:
            raise RuntimeError("Camera is not opened")

        result: Dict[str, Dict[str, Any]] = {}
        for logical_name, aliases in ADC_ATTR_ALIASES.items():
            real_name = self.resolve_attr_name(aliases)
            candidates = self.get_enum_candidates(aliases)
            result[logical_name] = {
                "attribute": real_name,
                "candidates": candidates,
            }
        return result

    def read_temperature_block(self) -> Tuple[Any, Any, Any]:
        if self.cam is None:
            raise RuntimeError("Camera is not opened")
        reading = self.get_attr_safe("Sensor Temperature Reading", default=None)
        setpoint = self.get_attr_safe("Sensor Temperature Set Point", default=None)
        status = self.get_attr_safe("Sensor Temperature Status", default=None)
        return reading, setpoint, status

    def get_temperature_setpoint_limits(self) -> Tuple[Optional[float], Optional[float]]:
        if self.cam is None:
            raise RuntimeError("Camera is not opened")
        attr = self.cam.get_attribute("Sensor Temperature Set Point", error_on_missing=False)
        if attr is None:
            return None, None
        try:
            attr.update_limits()
        except Exception:
            pass
        return getattr(attr, "min", None), getattr(attr, "max", None)

    def set_temperature_setpoint(self, target_c: float, clamp: bool = True) -> Tuple[Any, Any, Any]:
        """센서 목표 온도를 설정하고 (reading, setpoint, status)를 반환한다."""
        if self.cam is None:
            raise RuntimeError("Camera is not opened")

        attr = self.cam.get_attribute("Sensor Temperature Set Point", error_on_missing=False)
        if attr is None:
            raise RuntimeError("Sensor Temperature Set Point is not supported")

        target = float(target_c)
        if clamp:
            min_v, max_v = self.get_temperature_setpoint_limits()
            if min_v is not None and target < min_v:
                target = min_v
            if max_v is not None and target > max_v:
                target = max_v

        self.cam.set_attribute_value("Sensor Temperature Set Point", target)
        # Picam SDK는 set_attribute_value만으로는 하드웨어에 반영되지 않음.
        # Picam_CommitParameters를 호출해야 실제 적용된다.
        if hasattr(self.cam, "_commit_parameters"):
            self.cam._commit_parameters()
        return self.read_temperature_block()

    def wait_temperature_lock(
        self,
        timeout_s: float = 60.0,
        poll_s: float = 1.0,
        lock_keyword: str = "Locked",
    ) -> Tuple[bool, Any, Any, Any]:
        """
        온도 상태가 lock_keyword를 포함할 때까지 대기한다.
        반환: (locked, reading, setpoint, status)
        """
        if self.cam is None:
            raise RuntimeError("Camera is not opened")

        t0 = time.time()
        while True:
            reading, setpoint, status = self.read_temperature_block()
            if status is not None and lock_keyword in str(status):
                return True, reading, setpoint, status
            if (time.time() - t0) > float(timeout_s):
                return False, reading, setpoint, status
            time.sleep(max(float(poll_s), 0.05))

    def apply_temperature_settings(
        self,
        *,
        temperature_setpoint_c: Optional[float] = None,
        wait_lock: bool = False,
        timeout_s: float = 60.0,
    ) -> Dict[str, Any]:
        """온도 설정 적용/대기를 수행하고 결과 요약을 반환한다."""
        if self.cam is None:
            raise RuntimeError("Camera is not opened")

        report: Dict[str, Any] = {
            "supported": True,
            "setpoint_applied": False,
            "locked": None,
            "reading": None,
            "setpoint": None,
            "status": None,
            "min": None,
            "max": None,
        }

        min_v, max_v = self.get_temperature_setpoint_limits()
        if min_v is None and max_v is None:
            report["supported"] = False
            return report

        report["min"] = min_v
        report["max"] = max_v

        if temperature_setpoint_c is not None:
            reading, setpoint, status = self.set_temperature_setpoint(temperature_setpoint_c, clamp=True)
            report["setpoint_applied"] = True
            report["reading"] = reading
            report["setpoint"] = setpoint
            report["status"] = status
        else:
            reading, setpoint, status = self.read_temperature_block()
            report["reading"] = reading
            report["setpoint"] = setpoint
            report["status"] = status

        if wait_lock:
            locked, reading, setpoint, status = self.wait_temperature_lock(timeout_s=timeout_s)
            report["locked"] = locked
            report["reading"] = reading
            report["setpoint"] = setpoint
            report["status"] = status

        return report

    # ── 노출 / ROI ────────────────────────────────────────────────────

    def get_exposure_ms(self) -> float:
        """현재 노출시간을 ms 단위로 반환한다."""
        cam = _require_open_camera(self)
        return cam.get_exposure() * 1000.0

    def set_exposure_ms(self, exposure_ms: float) -> float:
        """노출시간(ms)을 설정하고 적용된 ms 값을 반환한다."""
        cam = _require_open_camera(self)
        cam.set_exposure(float(exposure_ms) / 1000.0)
        return cam.get_exposure() * 1000.0

    def get_roi(self):
        """현재 ROI를 반환한다."""
        cam = _require_open_camera(self)
        return cam.get_roi()

    def set_roi(
        self,
        hstart: int = 0,
        hend: Optional[int] = None,
        vstart: int = 0,
        vend: Optional[int] = None,
        hbin: int = 1,
        vbin: int = 1,
    ):
        """ROI를 설정하고 적용된 ROI를 반환한다."""
        cam = _require_open_camera(self)
        return cam.set_roi(hstart=hstart, hend=hend, vstart=vstart, vend=vend, hbin=hbin, vbin=vbin)

    # ── 이미지 획득 ───────────────────────────────────────────────────

    def snap(self):
        """이미지 1장을 취득한다."""
        cam = _require_open_camera(self)
        return cam.snap()

    def acquire_images(
        self,
        nframes: int,
        timeout_s: float = 10.0,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ):
        """이미지를 여러 장 취득해서 리스트로 반환한다."""
        cam = _require_open_camera(self)
        n = max(1, int(nframes))
        if n == 1:
            frame = cam.snap(timeout=float(timeout_s))
            if progress_cb is not None:
                progress_cb(1, 1)
            return [frame]
        frames = []
        cam.start_acquisition()
        try:
            for idx in range(n):
                if cam.wait_for_frame(timeout=float(timeout_s)):
                    frames.append(cam.read_oldest_image())
                    if progress_cb is not None:
                        progress_cb(idx + 1, n)
                else:
                    break
        finally:
            try:
                cam.stop_acquisition()
            except Exception:
                pass
        return frames

    def live_preview(
        self,
        frame_cb: Callable[[np.ndarray], None],
        timeout_s: float = 15,
        stop_condition: Optional[Callable[[], bool]] = None,
    ):
        """실시간 프리뷰를 수행한다. frame_cb에 새 프레임이 들어올 때마다 호출한다."""
        cam = _require_open_camera(self)
        cam.start_acquisition()
        try:
            while True:
                if stop_condition and stop_condition():
                    break
                if cam.wait_for_frame(timeout=timeout_s):
                    frame = cam.read_oldest_image()
                    frame_cb(frame)
        finally:
            try:
                cam.stop_acquisition()
            except Exception:
                pass

    # ── 저장 ─────────────────────────────────────────────────────────

    def save_as_spe(
        self,
        path,
        frames,
        *,
        exposure_ms: Optional[float] = None,
        roi=None,
        dtype=None,
        temperature_c: Optional[float] = None,
        adc_info: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """SPE 3.0 포맷으로 저장한다. 생략한 메타데이터는 현재 카메라 값으로 자동 보완한다."""
        if exposure_ms is None:
            try:
                exposure_ms = self.get_exposure_ms()
            except Exception:
                exposure_ms = 0.0
        if roi is None:
            try:
                roi = self.get_roi()
            except Exception:
                pass
        if temperature_c is None:
            try:
                reading, _, _ = self.read_temperature_block()
                if reading is not None:
                    temperature_c = float(reading)
            except Exception:
                pass
        if adc_info is None:
            try:
                cmap = self.get_adc_candidate_map()
                adc_info = {
                    key: self.get_attr_safe(meta["attribute"])
                    for key, meta in cmap.items()
                    if meta["attribute"]
                }
            except Exception:
                pass
        if metadata is None:
            metadata = self.get_spe_metadata(exposure_ms=exposure_ms, temperature_c=temperature_c, adc_info=adc_info)
        return _save_as_spe(
            path, frames,
            exposure_ms=exposure_ms,
            roi=roi,
            dtype=dtype,
            temperature_c=temperature_c,
            adc_info=adc_info,
            metadata=metadata,
        )

    def get_spe_metadata(
        self,
        *,
        exposure_ms: Optional[float] = None,
        temperature_c: Optional[float] = None,
        adc_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """SPE XML 푸터에 기록할 요약 메타데이터를 현재 카메라 상태에서 수집한다."""
        cam = _require_open_camera(self)
        metadata: Dict[str, Any] = {
            "created": datetime.now().astimezone().isoformat(),
            "software": "picamp",
            "software_version": "0.1",
        }

        try:
            info = cam.get_device_info()
            metadata["camera_model"] = getattr(info, "model", None)
            metadata["camera_serial"] = getattr(info, "serial_number", None)
            metadata["camera_interface"] = getattr(info, "interface", None)
            metadata["sensor_name"] = getattr(info, "name", None)
        except Exception:
            pass

        try:
            px_w_m, px_h_m = cam.get_pixel_size()
            metadata["pixel_size_um"] = (px_w_m * 1e6, px_h_m * 1e6)
        except Exception:
            pass

        sensor_characteristics = []
        sensor_type = self.get_attr_safe("Sensor Type", default=None)
        if sensor_type is not None:
            sensor_characteristics.append(str(sensor_type))

        if adc_info is None:
            try:
                cmap = self.get_adc_candidate_map()
                adc_info = {
                    key: self.get_attr_safe(meta["attribute"])
                    for key, meta in cmap.items()
                    if meta["attribute"]
                }
            except Exception:
                adc_info = None

        ports_used = None
        if adc_info:
            ports_used = adc_info.get("readout_ports_used")
        if ports_used is not None:
            try:
                sensor_characteristics.append("Multiport" if int(ports_used) > 1 else "SinglePort")
            except Exception:
                sensor_characteristics.append(f"Ports:{ports_used}")
        if sensor_characteristics:
            metadata["sensor_characteristics"] = ", ".join(sensor_characteristics)

        if exposure_ms is not None:
            metadata["exposure_time"] = float(exposure_ms)
            metadata["exposure_time_unit"] = "ms"

        try:
            _, setpoint, status = self.read_temperature_block()
            metadata["temperature_setpoint_c"] = setpoint
            metadata["temperature_status"] = status
        except Exception:
            pass

        if temperature_c is not None:
            metadata["temperature_reading_c"] = temperature_c

        if adc_info:
            metadata["adc_quality"] = adc_info.get("adc_quality")
            metadata["adc_analog_gain"] = adc_info.get("adc_analog_gain")
            metadata["bit_depth"] = adc_info.get("bit_depth")
            metadata["readout_ports_used"] = adc_info.get("readout_ports_used")
            readout_rate_mhz = _parse_first_float(adc_info.get("adc_speed"))
            if readout_rate_mhz is not None:
                metadata["readout_rate_mhz"] = readout_rate_mhz

        return metadata

    # ── 블록 설정 ─────────────────────────────────────────────────────

    def apply_camera_block(
        self,
        *,
        exposure_ms: Optional[float] = None,
        adc_enabled: bool = True,
        adc_quality: Any = None,
        adc_speed: Any = None,
        adc_analog_gain: Any = None,
        bit_depth: Any = None,
        readout_ports_used: Any = None,
        temperature_setpoint_c: Optional[float] = None,
        wait_temp_lock: bool = False,
        temp_lock_timeout_s: float = 60.0,
        include_adc_candidates: bool = False,
    ) -> Dict[str, Any]:
        """카메라 설정을 블록 형태로 한 번에 적용한다."""
        return apply_camera_block(
            self,
            exposure_ms=exposure_ms,
            adc_enabled=adc_enabled,
            adc_quality=adc_quality,
            adc_speed=adc_speed,
            adc_analog_gain=adc_analog_gain,
            bit_depth=bit_depth,
            readout_ports_used=readout_ports_used,
            temperature_setpoint_c=temperature_setpoint_c,
            wait_temp_lock=wait_temp_lock,
            temp_lock_timeout_s=temp_lock_timeout_s,
            include_adc_candidates=include_adc_candidates,
        )

    # ── ADC ───────────────────────────────────────────────────────────

    def apply_adc_settings(
        self,
        *,
        adc_quality: Any = None,
        adc_speed: Any = None,
        adc_analog_gain: Any = None,
        bit_depth: Any = None,
        readout_ports_used: Any = None,
    ) -> Dict[str, List[Any]]:
        if self.cam is None:
            raise RuntimeError("Camera is not opened")

        requested = {
            "adc_quality": adc_quality,
            "adc_speed": adc_speed,
            "adc_analog_gain": adc_analog_gain,
            "bit_depth": bit_depth,
            "readout_ports_used": readout_ports_used,
        }

        report: Dict[str, List[Any]] = {"applied": [], "missing": [], "skipped": []}

        for logical_name, value in requested.items():
            if value is None:
                report["skipped"].append(logical_name)
                continue

            aliases = ADC_ATTR_ALIASES[logical_name]
            real_name, current_value = _set_attr_by_aliases(self.cam, aliases, value)
            if real_name:
                report["applied"].append((logical_name, real_name, current_value))
            else:
                report["missing"].append(logical_name)

        return report


# ── Qt Live 워커 ───────────────────────────────────────────────────────────

class _LiveWorker(QObject):
    """별도 스레드에서 Picam live_preview를 돌리며 프레임 시그널 전달."""
    frame_ready = pyqtSignal(np.ndarray)

    def __init__(self, wrapper: PicamCameraWrapper):
        super().__init__()
        self._wrapper = wrapper
        self._stop_event = threading.Event()

    def run(self):
        self._stop_event.clear()
        self._wrapper.live_preview(
            frame_cb=lambda f: self.frame_ready.emit(np.asarray(f)),
            stop_condition=self._stop_event.is_set,
        )

    def stop(self):
        self._stop_event.set()


# ── PicamCamera (BaseCamera 구현) ──────────────────────────────────────────

class PicamCamera(BaseCamera):
    """
    Princeton Instruments Picam 카메라 (pylablib 경유).

    사용법:
        cam = PicamCamera(serial_number=None)   # None = 첫 번째 발견된 카메라
        cam.connect()
        cam.start_live(lambda frame: ...)
        cam.stop_live()
        cam.disconnect()
    """

    def __init__(self, serial_number: Optional[str] = None):
        self._serial_number = serial_number
        self._wrapper: Optional[PicamCameraWrapper] = None
        self._worker: Optional[_LiveWorker] = None
        self._thread: Optional[QThread] = None
        self._connected = False
        self._live = False
        self._caps: Optional[CameraCapabilities] = None
        self._frame_cb: Optional[Callable] = None

    # ── BaseCamera 구현 ───────────────────────────────────────────────

    @property
    def capabilities(self) -> CameraCapabilities:
        if self._caps is not None:
            return self._caps

        caps = CameraCapabilities(
            has_roi=True,
            exposure_range_ms=(0.001, 3_600_000.0),
            has_temperature=False,
            temperature_range_c=(None, None),
            has_adc=False,
        )

        if self._connected and self._wrapper is not None:
            try:
                mn, mx = self._wrapper.get_temperature_setpoint_limits()
                if mn is not None or mx is not None:
                    caps.has_temperature = True
                    caps.temperature_range_c = (mn, mx)
            except Exception:
                pass

            try:
                adc_map = self._wrapper.get_adc_candidate_map()
                has_any = any(
                    len(v.get("candidates", [])) > 0
                    for v in adc_map.values()
                )
                if has_any:
                    caps.has_adc = True
                    caps.adc_quality_options   = adc_map.get("adc_quality",        {}).get("candidates", [])
                    caps.adc_speed_options     = adc_map.get("adc_speed",          {}).get("candidates", [])
                    caps.adc_gain_options      = adc_map.get("adc_analog_gain",    {}).get("candidates", [])
                    caps.adc_bit_depth_options = adc_map.get("bit_depth",          {}).get("candidates", [])
                    caps.adc_port_options      = adc_map.get("readout_ports_used", {}).get("candidates", [])
            except Exception:
                pass

        self._caps = caps
        return caps

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        if not _PICAM_OK:
            raise RuntimeError(f"picamp 모듈을 불러올 수 없습니다: {_PICAM_IMPORT_ERROR}")

        self._wrapper = PicamCameraWrapper(serial_number=self._serial_number)
        self._wrapper.open()
        self._connected = True
        self._caps = None

    def disconnect(self) -> None:
        if self._live:
            self.stop_live()
        if self._wrapper is not None:
            try:
                self._wrapper.close()
            except Exception:
                pass
        self._connected = False
        self._wrapper = None
        self._caps = None

    def get_exposure_ms(self) -> float:
        self._require_connected()
        return self._wrapper.get_exposure_ms()

    def set_exposure_ms(self, ms: float) -> float:
        self._require_connected()
        return self._wrapper.set_exposure_ms(ms)

    def snap(self) -> np.ndarray:
        self._require_connected()
        return np.asarray(self._wrapper.snap())

    def start_live(self, frame_cb: Callable[[np.ndarray], None]) -> None:
        self._require_connected()
        if self._live:
            return

        self._frame_cb = frame_cb
        self._thread = QThread()
        self._worker = _LiveWorker(self._wrapper)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(frame_cb)
        self._thread.start()
        self._live = True

    def stop_live(self) -> None:
        if not self._live:
            return
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            if not self._thread.wait(5000):
                self._thread.terminate()
        self._worker = None
        self._thread = None
        self._live = False

    # ── ROI ───────────────────────────────────────────────────────────

    def set_roi(self, x: int, y: int, width: int, height: int,
                hbin: int = 1, vbin: int = 1) -> None:
        self._require_connected()
        self._wrapper.set_roi(
            hstart=x, hend=x + width,
            vstart=y, vend=y + height,
            hbin=hbin, vbin=vbin,
        )

    def get_roi(self) -> Optional[tuple]:
        self._require_connected()
        return self._wrapper.get_roi()

    # ── 온도 ──────────────────────────────────────────────────────────

    def set_temperature(self, celsius: float) -> None:
        self._require_connected()
        if not self.capabilities.has_temperature:
            raise NotSupportedError("Temperature control not supported")
        # Picam SDK는 acquisition 중 파라미터 변경을 무시함 → 잠깐 정지 후 재시작
        was_live = self._live
        if was_live:
            self.stop_live()
        self._wrapper.set_temperature_setpoint(celsius)
        if was_live and self._frame_cb is not None:
            self.start_live(self._frame_cb)

    def get_temperature(self) -> tuple:
        self._require_connected()
        if not self.capabilities.has_temperature:
            raise NotSupportedError("Temperature control not supported")
        return self._wrapper.read_temperature_block()

    # ── ADC ───────────────────────────────────────────────────────────

    def set_adc_settings(self, **kwargs: Any) -> None:
        self._require_connected()
        if not self.capabilities.has_adc:
            raise NotSupportedError("ADC settings not supported")
        self._wrapper.apply_adc_settings(**kwargs)

    def get_adc_candidates(self) -> dict:
        self._require_connected()
        return self._wrapper.get_adc_candidate_map()

    # ── 카메라 정보 ───────────────────────────────────────────────────

    def camera_name(self) -> str:
        if self._connected and self._wrapper is not None:
            try:
                info = self._wrapper.cam.get_device_info()
                return getattr(info, "model", "Picam")
            except Exception:
                pass
        return "Picam"

    def camera_model(self) -> str:
        return self.camera_name()

    def camera_serial(self) -> str:
        if self._connected and self._wrapper is not None:
            try:
                info = self._wrapper.cam.get_device_info()
                return str(getattr(info, "serial_number", ""))
            except Exception:
                pass
        return self._serial_number or ""

    # ── SPE 저장 헬퍼 ─────────────────────────────────────────────────

    def save_as_spe(self, path, frames, **kwargs):
        """picamp의 save_as_spe를 통해 풍부한 메타데이터와 함께 저장."""
        self._require_connected()
        return self._wrapper.save_as_spe(path, frames, **kwargs)

    # ── 내부 ──────────────────────────────────────────────────────────

    def _require_connected(self):
        if not self._connected or self._wrapper is None:
            raise RuntimeError("카메라가 연결되지 않았습니다")


# ── 모듈 레벨 편의 함수 ────────────────────────────────────────────────────

def _as_wrapper(cam_or_wrapper) -> PicamCameraWrapper:
    if isinstance(cam_or_wrapper, PicamCameraWrapper):
        return cam_or_wrapper
    return PicamCameraWrapper(cam=cam_or_wrapper)


def _require_open_camera(wrapper: PicamCameraWrapper):
    if wrapper.cam is None:
        raise RuntimeError("Camera is not opened")
    return wrapper.cam


def is_available() -> bool:
    """pylablib/Picam 드라이버를 사용할 수 있으면 True."""
    return _PICAM_OK


def list_devices() -> List[str]:
    """연결 가능한 Picam 카메라 목록을 문자열 리스트로 반환."""
    if not _PICAM_OK:
        return []
    try:
        cams = PrincetonInstruments.list_cameras()
        return [str(c) for c in cams]
    except Exception:
        return []


def list_cameras():
    """연결 가능한 카메라 목록을 반환한다."""
    return PrincetonInstruments.list_cameras()


def open_camera(serial_number: Optional[str] = None) -> PicamCameraWrapper:
    """카메라를 열고 PicamCameraWrapper를 반환한다."""
    wrapper = PicamCameraWrapper(serial_number=serial_number)
    wrapper.open()
    return wrapper


def close_camera(cam_or_wrapper) -> None:
    """카메라를 안전하게 close한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    wrapper.close()


def set_exposure_ms(cam_or_wrapper, exposure_ms: float) -> float:
    """노출시간(ms)을 설정하고 적용된 ms 값을 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    cam = _require_open_camera(wrapper)
    cam.set_exposure(float(exposure_ms) / 1000.0)
    return cam.get_exposure() * 1000.0


def get_exposure_ms(cam_or_wrapper) -> float:
    """현재 노출시간을 ms 단위로 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    cam = _require_open_camera(wrapper)
    return cam.get_exposure() * 1000.0


def get_roi(cam_or_wrapper):
    """현재 ROI를 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    cam = _require_open_camera(wrapper)
    return cam.get_roi()


def set_roi(
    cam_or_wrapper,
    hstart: int = 0,
    hend: Optional[int] = None,
    vstart: int = 0,
    vend: Optional[int] = None,
    hbin: int = 1,
    vbin: int = 1,
):
    """ROI를 설정하고 적용된 ROI를 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    cam = _require_open_camera(wrapper)
    return cam.set_roi(hstart=hstart, hend=hend, vstart=vstart, vend=vend, hbin=hbin, vbin=vbin)


def get_adc_candidate_map(cam_or_wrapper) -> Dict[str, Dict[str, Any]]:
    """ADC 관련 후보값 맵을 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    return wrapper.get_adc_candidate_map()


def apply_adc_settings(
    cam_or_wrapper,
    *,
    adc_quality: Any = None,
    adc_speed: Any = None,
    adc_analog_gain: Any = None,
    bit_depth: Any = None,
    readout_ports_used: Any = None,
) -> Dict[str, List[Any]]:
    """ADC 파라미터를 적용한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    return wrapper.apply_adc_settings(
        adc_quality=adc_quality,
        adc_speed=adc_speed,
        adc_analog_gain=adc_analog_gain,
        bit_depth=bit_depth,
        readout_ports_used=readout_ports_used,
    )


def read_temperature_block(cam_or_wrapper) -> Tuple[Any, Any, Any]:
    """(reading, setpoint, status)를 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    return wrapper.read_temperature_block()


def set_temperature_setpoint(cam_or_wrapper, target_c: float, clamp: bool = True) -> Tuple[Any, Any, Any]:
    """온도 setpoint를 설정하고 (reading, setpoint, status)를 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    return wrapper.set_temperature_setpoint(target_c=target_c, clamp=clamp)


def wait_temperature_lock(
    cam_or_wrapper,
    timeout_s: float = 60.0,
    poll_s: float = 1.0,
    lock_keyword: str = "Locked",
) -> Tuple[bool, Any, Any, Any]:
    """온도 lock 대기."""
    wrapper = _as_wrapper(cam_or_wrapper)
    return wrapper.wait_temperature_lock(timeout_s=timeout_s, poll_s=poll_s, lock_keyword=lock_keyword)


def apply_temperature_settings(
    cam_or_wrapper,
    *,
    temperature_setpoint_c: Optional[float] = None,
    wait_lock: bool = False,
    timeout_s: float = 60.0,
) -> Dict[str, Any]:
    """온도 설정을 적용/대기한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    return wrapper.apply_temperature_settings(
        temperature_setpoint_c=temperature_setpoint_c,
        wait_lock=wait_lock,
        timeout_s=timeout_s,
    )


def snap_image(cam_or_wrapper):
    """이미지 1장을 취득한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    cam = _require_open_camera(wrapper)
    return cam.snap()


def _save_as_spe(
    path,
    frames,
    *,
    exposure_ms: float = 0.0,
    roi=None,
    dtype=None,
    temperature_c: Optional[float] = None,
    adc_info: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    SPE 3.0 포맷으로 이미지를 저장한다 (LightField / spe_loader 호환).

    Parameters
    ----------
    path        : 저장 경로 (str or Path), .spe 확장자 권장
    frames      : ndarray (H, W) 또는 (N, H, W), 또는 list of ndarray
    exposure_ms : 노출 시간 (ms), 메타데이터에 기록
    roi         : (hstart, hend, vstart, vend, hbin, vbin) — get_roi() 반환값
    dtype       : 저장 데이터 타입 (None=원본 유지)

    Returns
    -------
    저장된 파일의 Path
    """
    if isinstance(frames, np.ndarray):
        frames = [frames[i] for i in range(frames.shape[0])] if frames.ndim == 3 else [frames]
    frames = list(frames)
    nframes = len(frames)
    if nframes == 0:
        raise ValueError("frames is empty")

    frame0 = np.asarray(frames[0])
    height, width = frame0.shape

    out_dtype = np.dtype(dtype) if dtype is not None else frame0.dtype
    dtype_code = _SPE_DTYPE_CODE.get(out_dtype.name, 3)
    if out_dtype.name not in _SPE_DTYPE_CODE:
        out_dtype = np.dtype("uint16")
        dtype_code = 3
    pixel_format = _SPE_PIXEL_FORMAT.get(out_dtype.name, "MonochromeUnsigned16")

    bytes_per_px = out_dtype.itemsize
    frame_bytes = width * height * bytes_per_px

    x0, y0, hbin, vbin = 0, 0, 1, 1
    if roi is not None:
        x0   = int(roi[0])
        y0   = int(roi[2])
        hbin = int(roi[4]) if len(roi) > 4 else 1
        vbin = int(roi[5]) if len(roi) > 5 else 1

    header = bytearray(4100)
    footer_offset = 4100 + nframes * frame_bytes
    struct.pack_into("<H", header, 42, min(width, 65535))
    struct.pack_into("<H", header, 656, min(height, 65535))
    struct.pack_into("<h", header, 108, dtype_code)
    struct.pack_into("<i", header, 1446, nframes)
    struct.pack_into("<f", header, 1992, 3.0)
    struct.pack_into("<Q", header, 678, footer_offset)

    metadata = dict(metadata or {})
    created_str = metadata.get("created") or datetime.now().astimezone().isoformat()
    camera_name = metadata.get("camera_model") or "Camera1"
    pixel_size_um = metadata.get("pixel_size_um")
    pixel_xml = ""
    if isinstance(pixel_size_um, (tuple, list)) and len(pixel_size_um) >= 2:
        pixel_xml = (
            "<Pixel>"
            f'<Width>{float(pixel_size_um[0]):.6f}</Width>'
            f'<Height>{float(pixel_size_um[1]):.6f}</Height>'
            "</Pixel>"
        )

    temperature_reading_c = metadata.get("temperature_reading_c", temperature_c)
    temperature_setpoint_c = metadata.get("temperature_setpoint_c")
    temperature_status = metadata.get("temperature_status")
    if temperature_reading_c is not None or temperature_setpoint_c is not None or temperature_status is not None:
        reading_xml = (
            f'<Reading r:readOnly="true">{float(temperature_reading_c):.4f}</Reading>'
            if temperature_reading_c is not None else ""
        )
        temp_xml = (
            "<Sensor>"
            f'{_xml_tag("SensorName", metadata.get("sensor_name"))}'
            f'{_xml_tag("CcdCharacteristics", metadata.get("sensor_characteristics"))}'
            "<Temperature>"
            f'{_xml_tag("SetPoint", temperature_setpoint_c)}'
            f"{reading_xml}"
            f'{_xml_tag("SensorTemperature", temperature_status)}'
            "</Temperature>"
            "</Sensor>"
        )
    else:
        temp_xml = ""

    adc_xml = ""
    if adc_info:
        readout_rate_mhz = metadata.get("readout_rate_mhz")
        adc_xml = (
            "<ADC>"
            f'{_xml_tag("Quality", adc_info.get("adc_quality"))}'
            f'{_xml_tag("Speed", adc_info.get("adc_speed"))}'
            f'{_xml_tag("ReadoutRate", readout_rate_mhz)}'
            f'{_xml_tag("AnalogGain", adc_info.get("adc_analog_gain"))}'
            f'{_xml_tag("BitDepth", adc_info.get("bit_depth"))}'
            f'{_xml_tag("ReadoutPortsUsed", adc_info.get("readout_ports_used"))}'
            "</ADC>"
        )

    roi_xml = (
        "<ReadoutControl><RegionsOfInterest><CustomRegions>"
        '<RegionOfInterest>'
        f'<X>{x0}</X><Y>{y0}</Y><Width>{width}</Width><Height>{height}</Height>'
        f'<XBinning>{hbin}</XBinning><YBinning>{vbin}</YBinning>'
        '</RegionOfInterest>'
        "</CustomRegions></RegionsOfInterest></ReadoutControl>"
    )

    exposure_block_xml = (
        "<ShutterTiming>"
        f'<ExposureTime>{float(metadata.get("exposure_time", exposure_ms)):.6f}</ExposureTime>'
        f'<TimeUnit>{escape(str(metadata.get("exposure_time_unit", "ms")))}</TimeUnit>'
        "</ShutterTiming>"
    )

    xml_footer = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<SpeFormat version="3.0" xmlns:r="http://www.teledynevisionsolutions.com/spe/readonly">'
        "<DataFormat>"
        f'<DataBlock type="Frame" count="{nframes}" size="{frame_bytes}" stride="{frame_bytes}" pixelFormat="{metadata.get("pixel_format", pixel_format)}">'
        f'<DataBlock type="Region" count="1" size="{frame_bytes}" stride="{frame_bytes}" width="{width}" height="{height}"/>'
        "</DataBlock>"
        "</DataFormat>"
        "<DataHistories><DataHistory><Origin "
        f'created="{escape(created_str)}" '
        f'software="{escape(str(metadata.get("software", "picamp")))}" '
        f'softwareVersion="{escape(str(metadata.get("software_version", "0.1")))}">'
        "<Experiment><Devices><Cameras>"
        f'<Camera name="{escape(str(camera_name))}" '
        f'model="{escape(str(metadata.get("camera_model", camera_name)))}" '
        f'serialNumber="{escape(str(metadata.get("camera_serial", "")))}" '
        f'computerInterface="{escape(str(metadata.get("camera_interface", "")))}">'
        f"{exposure_block_xml}"
        f"{roi_xml}"
        f"{pixel_xml}"
        f"{adc_xml}"
        f"{temp_xml}"
        f'{_xml_tag("TriggerResponse", metadata.get("trigger_response"))}'
        "</Camera>"
        "</Cameras></Devices></Experiment>"
        "</Origin></DataHistory></DataHistories>"
        "</SpeFormat>"
    )

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(bytes(header))
        for frame in frames:
            f.write(np.asarray(frame, dtype=out_dtype).tobytes())
        f.write(xml_footer.encode("utf-8"))

    return out_path


save_as_spe = _save_as_spe


def acquire_images(
    cam_or_wrapper,
    nframes: int,
    timeout_s: float = 10.0,
    progress_cb: Optional[Callable[[int, int], None]] = None,
):
    """이미지를 여러 장 취득해서 리스트로 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    cam = _require_open_camera(wrapper)

    n = max(1, int(nframes))
    if n == 1:
        frame = cam.snap()
        if progress_cb is not None:
            progress_cb(1, 1)
        return [frame]

    frames = []
    cam.start_acquisition()
    try:
        for idx in range(n):
            if cam.wait_for_frame(timeout=float(timeout_s)):
                frames.append(cam.read_oldest_image())
                if progress_cb is not None:
                    progress_cb(idx + 1, n)
            else:
                break
    finally:
        try:
            cam.stop_acquisition()
        except Exception:
            pass
    return frames


def apply_camera_block(
    cam_or_wrapper,
    *,
    exposure_ms: Optional[float] = None,
    adc_enabled: bool = True,
    adc_quality: Any = None,
    adc_speed: Any = None,
    adc_analog_gain: Any = None,
    bit_depth: Any = None,
    readout_ports_used: Any = None,
    temperature_setpoint_c: Optional[float] = None,
    wait_temp_lock: bool = False,
    temp_lock_timeout_s: float = 60.0,
    include_adc_candidates: bool = False,
) -> Dict[str, Any]:
    """카메라 설정을 블록 형태로 한 번에 적용한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    report: Dict[str, Any] = {
        "exposure_ms": None,
        "adc": {"applied": [], "missing": [], "skipped": []},
        "temperature": None,
    }

    if exposure_ms is not None:
        report["exposure_ms"] = set_exposure_ms(wrapper, exposure_ms)

    if adc_enabled:
        report["adc"] = wrapper.apply_adc_settings(
            adc_quality=adc_quality,
            adc_speed=adc_speed,
            adc_analog_gain=adc_analog_gain,
            bit_depth=bit_depth,
            readout_ports_used=readout_ports_used,
        )

    report["temperature"] = wrapper.apply_temperature_settings(
        temperature_setpoint_c=temperature_setpoint_c,
        wait_lock=wait_temp_lock,
        timeout_s=temp_lock_timeout_s,
    )

    if include_adc_candidates:
        report["adc_candidates"] = wrapper.get_adc_candidate_map()

    return report


# ── 하위호환 aliases ───────────────────────────────────────────────────────

wrapper_list_cameras = list_cameras
wrapper_open_camera = open_camera
wrapper_close_camera = close_camera
wrapper_get_exposure_ms = get_exposure_ms
wrapper_set_exposure_ms = set_exposure_ms
wrapper_get_roi = get_roi
wrapper_set_roi = set_roi
wrapper_get_adc_candidate_map = get_adc_candidate_map
wrapper_apply_adc_settings = apply_adc_settings
wrapper_read_temperature_block = read_temperature_block
wrapper_set_temperature_setpoint = set_temperature_setpoint
wrapper_wait_temperature_lock = wait_temperature_lock
wrapper_apply_temperature_settings = apply_temperature_settings
wrapper_snap_image = snap_image
wrapper_acquire_images = acquire_images
wrapper_save_as_spe = save_as_spe
wrapper_apply_camera_block = apply_camera_block
open_camera_wrapper = open_camera
close_camera_wrapper = close_camera
apply_adc_settings_fn = apply_adc_settings
apply_temperature_settings_fn = apply_temperature_settings
