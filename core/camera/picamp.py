import time
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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
from core.spe_writer import save_spe as _spe_writer_save_spe


# 모델별로 속성명이 달라질 수 있어 alias 목록을 둔다.
ADC_ATTR_ALIASES = {
    "adc_quality": ["Adc Quality", "ADC Quality"],
    "adc_speed": ["Adc Speed", "ADC Speed"],
    "adc_analog_gain": ["Adc Analog Gain", "ADC Analog Gain"],
    "bit_depth": ["Bit Depth", "Adc Bit Depth", "ADC Bit Depth"],
    "readout_ports_used": ["Readout Ports Used", "Readout Port Count"],
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
        """온도 블록 읽기.

        Reading / Status 는 Picam SDK의 ReadParameter 계열만 동작하며
        GetParameter(get_attribute_value)로 호출하면 SDK가 에러를 반환한다.
        clib 직접 호출 → pylablib read_attribute_value → get_attribute_value 순 폴백.
        """
        if self.cam is None:
            raise RuntimeError("Camera is not opened")

        reading = self._read_hw_float("Sensor Temperature Reading")
        setpoint = self.get_attr_safe("Sensor Temperature Set Point", default=None)
        status = self._read_hw_int_str("Sensor Temperature Status")
        return reading, setpoint, status

    def _read_hw_float(self, name: str) -> Optional[float]:
        """Picam_ReadParameterFloatingPointValue 경유 하드웨어 직접 읽기."""
        import ctypes
        # 1) pylablib read_attribute_value (있는 경우)
        if hasattr(self.cam, 'read_attribute_value'):
            try:
                return self.cam.read_attribute_value(name)
            except Exception:
                pass
        # 2) clib 직접 호출
        try:
            attr = self.cam.get_attribute(name, error_on_missing=False)
            if attr is None:
                return None
            pid = getattr(attr, 'pid', None) or getattr(attr, 'parameter', None) or getattr(attr, 'param', None)
            if pid is None:
                return None
            handle = getattr(self.cam, 'handle', None) or getattr(self.cam, '_cam', None)
            val = ctypes.c_double()
            err = self.cam.clib.Picam_ReadParameterFloatingPointValue(
                handle, ctypes.c_int(int(pid)), ctypes.byref(val)
            )
            return val.value if err == 0 else None
        except Exception:
            pass
        # 3) 폴백 (일부 구성에선 동작할 수 있음)
        return self.get_attr_safe(name, default=None)

    def _read_hw_int_str(self, name: str) -> Optional[Any]:
        """Picam_ReadParameterIntegerValue 경유 하드웨어 직접 읽기 → 문자열 변환."""
        import ctypes
        # 1) pylablib read_attribute_value
        if hasattr(self.cam, 'read_attribute_value'):
            try:
                return self.cam.read_attribute_value(name)
            except Exception:
                pass
        # 2) clib 직접 호출
        try:
            attr = self.cam.get_attribute(name, error_on_missing=False)
            if attr is None:
                return None
            pid = getattr(attr, 'pid', None) or getattr(attr, 'parameter', None) or getattr(attr, 'param', None)
            if pid is None:
                return None
            handle = getattr(self.cam, 'handle', None) or getattr(self.cam, '_cam', None)
            val = ctypes.c_int()
            err = self.cam.clib.Picam_ReadParameterIntegerValue(
                handle, ctypes.c_int(int(pid)), ctypes.byref(val)
            )
            if err != 0:
                return None
            # enum → 문자열: pylablib attribute의 ivalues/values 맵 활용
            ivalues = getattr(attr, 'ivalues', None)
            values  = getattr(attr, 'values',  None)
            if ivalues and values:
                try:
                    idx = list(ivalues).index(val.value)
                    return list(values)[idx]
                except (ValueError, IndexError):
                    pass
            return val.value
        except Exception:
            pass
        # 3) 폴백
        return self.get_attr_safe(name, default=None)

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
        # set_attribute_value 단독으로는 하드웨어에 반영 안 됨 — Commit 필요
        if hasattr(cam, "_commit_parameters"):
            cam._commit_parameters()
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
        result = cam.set_roi(hstart=hstart, hend=hend, vstart=vstart, vend=vend, hbin=hbin, vbin=vbin)
        if hasattr(cam, "_commit_parameters"):
            cam._commit_parameters()
        return result

    # ── 이미지 획득 ───────────────────────────────────────────────────

    def _get_frame_total_s(self) -> float:
        """노출 시간 + 리드아웃 시간(초)를 반환한다.

        Picam SDK의 'Readout Time Calculation' 파라미터(ms)를 우선 사용하고,
        조회 실패 시 노출 시간만 반환한다.
        """
        try:
            exp_s = self.get_exposure_ms() / 1000.0
        except Exception:
            exp_s = 1.0

        readout_s = 0.0
        if self.cam is not None:
            for name in ("Readout Time Calculation", "ReadoutTimeCalculation",
                         "Readout Time", "Frame Rate Calculation"):
                val = _get_attr_safe(self.cam, name)
                if val is not None:
                    ms = _parse_first_float(val)
                    if ms is not None:
                        # Picam SDK는 ms 단위; Frame Rate는 fps → 역수
                        if "Rate" in name and ms > 0:
                            readout_s = max(1.0 / ms - exp_s, 0.0)
                        else:
                            readout_s = ms / 1000.0
                        break

        return exp_s + readout_s

    def _auto_timeout(self, margin: float = 5.0, multiplier: float = 2.0,
                      minimum: float = 10.0) -> float:
        """프레임 총 시간 기반 자동 타임아웃(초)을 계산한다."""
        return max(self._get_frame_total_s() * multiplier + margin, minimum)

    def snap(self, timeout: Optional[float] = None):
        """이미지 1장을 취득한다. timeout=None이면 노출+리드아웃 기반 자동 계산."""
        cam = _require_open_camera(self)
        # 혹시 이전 acquisition 상태가 남아있을 경우 먼저 정리
        try:
            cam.stop_acquisition()
        except Exception:
            pass
        if timeout is None:
            timeout = self._auto_timeout()
        return cam.snap(timeout=float(timeout))

    def acquire_images(
        self,
        nframes: int,
        timeout_s: Optional[float] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ):
        """이미지를 여러 장 취득해서 리스트로 반환한다.
        timeout_s=None이면 노출+리드아웃 기반 자동 계산."""
        cam = _require_open_camera(self)
        n = max(1, int(nframes))
        # 혹시 이전 acquisition 상태가 남아있을 경우 먼저 정리
        try:
            cam.stop_acquisition()
        except Exception:
            pass
        if timeout_s is None:
            timeout_s = self._auto_timeout()
        if n == 1:
            frame = cam.snap(timeout=float(timeout_s))
            if progress_cb is not None:
                progress_cb(1, 1)
            return [frame]
        frames = []
        cam.start_acquisition()
        try:
            for idx in range(n):
                try:
                    got = cam.wait_for_frame(timeout=float(timeout_s))
                except Exception:
                    got = False
                if got:
                    frames.append(cam.read_oldest_image())
                    if progress_cb is not None:
                        progress_cb(idx + 1, n)
                else:
                    raise RuntimeError(
                        f"프레임 {idx+1}/{n} 대기 타임아웃 ({timeout_s:.1f}초) — "
                        "Timeout 설정 또는 노출 시간을 확인하세요"
                    )
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
        # 이전 acquisition 상태가 남아있을 경우 먼저 정리
        try:
            cam.stop_acquisition()
        except Exception:
            pass

        # poll 간격 = 노출+리드아웃 + 1초 여유, 최소 2초
        # (프레임 총 시간보다 짧으면 pylablib이 TimeoutError를 발생시킴)
        poll_s = max(self._get_frame_total_s() + 1.0, 2.0)

        cam.start_acquisition()
        try:
            while True:
                if stop_condition and stop_condition():
                    break
                try:
                    got_frame = cam.wait_for_frame(timeout=poll_s)
                except Exception:
                    # pylablib은 타임아웃 시 False 대신 예외를 던지기도 함 — 무시하고 계속
                    if stop_condition and stop_condition():
                        break
                    continue
                if got_frame:
                    if stop_condition and stop_condition():
                        break
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
        extra_metadata: Optional[Dict[str, Any]] = None,
        **_,
    ) -> Path:
        """저장 전 카메라에서 가능한 모든 정보를 읽고 spe_writer.save_spe로 저장한다."""
        # ── 노출 / ROI ────────────────────────────────────────────────
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

        cam = _require_open_camera(self)

        # ── 카메라 기본 정보 ──────────────────────────────────────────
        cam_name = cam_model = cam_serial = cam_iface = ""
        pixel_size_um = None
        try:
            info = cam.get_device_info()
            cam_name   = str(getattr(info, "model", "") or "")
            cam_model  = cam_name
            cam_serial = str(getattr(info, "serial_number", "") or "")
            cam_iface  = str(getattr(info, "interface", "") or "")
        except Exception:
            pass
        try:
            pw, ph = cam.get_pixel_size()
            pixel_size_um = (pw * 1e6, ph * 1e6)
        except Exception:
            pass

        # ── 센서 이름 ─────────────────────────────────────────────────
        # Picam SDK는 "Sensor Name" 속성으로 센서 이름을 제공한다.
        # device_info에서 못 읽으면 SDK 속성으로 폴백.
        sensor_name: Optional[str] = None
        for _attr in ["Sensor Name", "SensorName"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v:
                sensor_name = str(_v)
                break
        if not sensor_name:
            try:
                _info = cam.get_device_info()
                sensor_name = (str(getattr(_info, "sensor_name", "") or "")
                               or str(getattr(_info, "name", "") or "")) or None
            except Exception:
                pass

        # ── 센서 타입 / CCD 특성 ──────────────────────────────────────
        sensor_type: Optional[str] = None
        for _attr in ["Sensor Type", "SensorType"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v:
                sensor_type = str(_v)
                break

        sensor_chars: Optional[str] = None
        for _attr in ["Sensor CCD Characteristics", "CCD Characteristics", "Ccd Characteristics"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v:
                sensor_chars = str(_v)
                break
        # 항상 _parts를 초기화해두고, SDK에서 못 읽었을 때 조합용으로 사용
        _parts: List[str] = []
        if not sensor_chars and sensor_type:
            _parts.append(sensor_type)

        # ── 온도 ─────────────────────────────────────────────────────
        temp_reading = temp_setpoint = temp_status = None
        try:
            temp_reading, temp_setpoint, temp_status = self.read_temperature_block()
        except Exception:
            pass

        # ── ShutterTiming 추가 정보 ───────────────────────────────────
        shutter_mode: Optional[str] = None
        shutter_opening_delay_ms: Optional[float] = None
        shutter_closing_delay_ms: Optional[float] = None
        for _attr in ["Shutter Timing Mode", "ShutterTimingMode"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v is not None:
                shutter_mode = str(_v)
                break
        for _attr in ["Shutter Opening Delay", "Opening Delay"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v is not None:
                shutter_opening_delay_ms = _parse_first_float(_v)
                break
        for _attr in ["Shutter Closing Delay", "Closing Delay"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v is not None:
                shutter_closing_delay_ms = _parse_first_float(_v)
                break

        # ── ReadoutControl 추가 정보 ──────────────────────────────────
        readout_mode: Optional[str] = None
        vertical_shift_rate: Optional[float] = None
        for _attr in ["Readout Control Mode", "ReadoutControlMode"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v is not None:
                readout_mode = str(_v)
                break
        for _attr in ["Vertical Shift Rate", "VerticalShiftRate"]:
            _v = self.get_attr_safe(_attr, default=None)
            if _v is not None:
                vertical_shift_rate = _parse_first_float(_v)
                break

        # ── ADC ───────────────────────────────────────────────────────
        adc_info: Optional[Dict[str, Any]] = None
        readout_rate_mhz: Optional[float] = None
        _ports_used: Optional[int] = None
        try:
            cmap = self.get_adc_candidate_map()
            adc_info = {
                key: self.get_attr_safe(meta["attribute"])
                for key, meta in cmap.items()
                if meta["attribute"]
            }
            _p = adc_info.get("readout_ports_used")
            if _p is not None:
                try:
                    _ports_used = int(_p)
                    if not sensor_chars:  # SDK에서 직접 못 읽었을 때만 조합
                        _parts.append("Multiport" if _ports_used > 1 else "SinglePort")
                except Exception:
                    pass
            readout_rate_mhz = _parse_first_float(adc_info.get("adc_speed"))
        except Exception:
            pass

        if not sensor_chars and _parts:
            sensor_chars = ", ".join(_parts) or None

        return _spe_writer_save_spe(
            path, frames,
            exposure_ms=float(exposure_ms),
            roi=roi,
            dtype=dtype,
            camera_name=cam_name or "Picam",
            camera_model=cam_model or cam_name or "Picam",
            camera_serial=cam_serial,
            camera_interface=cam_iface,
            pixel_size_um=pixel_size_um,
            sensor_name=sensor_name,
            sensor_type=sensor_type,
            sensor_characteristics=sensor_chars,
            temperature_reading_c=temp_reading,
            temperature_setpoint_c=temp_setpoint,
            temperature_status=temp_status,
            shutter_mode=shutter_mode,
            shutter_opening_delay_ms=shutter_opening_delay_ms,
            shutter_closing_delay_ms=shutter_closing_delay_ms,
            readout_mode=readout_mode,
            readout_ports_used=_ports_used,
            vertical_shift_rate=vertical_shift_rate,
            adc_info=adc_info,
            readout_rate_mhz=readout_rate_mhz,
            software="picamp",
            software_version="0.1",
            extra_metadata=extra_metadata,
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

        # 모든 ADC 파라미터 설정 후 한 번에 Commit
        if report["applied"] and hasattr(self.cam, "_commit_parameters"):
            self.cam._commit_parameters()

        return report


# ── Qt Live 워커 ───────────────────────────────────────────────────────────

class _LiveWorker(QObject):
    """별도 스레드에서 Picam live_preview를 돌리며 프레임 시그널 전달."""
    frame_ready = pyqtSignal(np.ndarray)
    error       = pyqtSignal(str)

    def __init__(self, wrapper: PicamCameraWrapper):
        super().__init__()
        self._wrapper = wrapper
        self._stop_event = threading.Event()

    def run(self):
        self._stop_event.clear()
        try:
            self._wrapper.live_preview(
                frame_cb=lambda f: self.frame_ready.emit(np.asarray(f)),
                stop_condition=self._stop_event.is_set,
            )
        except Exception as e:
            self.error.emit(str(e))

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
        # timeout=None → PicamCameraWrapper._auto_timeout() 자동 계산
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
        self._worker.error.connect(
            lambda msg: print(f"[PicamCamera] Live 오류: {msg}")
        )
        self._thread.start()
        self._live = True

    def stop_live(self) -> None:
        if not self._live:
            return
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            if not self._thread.wait(3000):
                self._thread.terminate()
                self._thread.wait()  # terminate 후 실제 종료까지 대기 (Destroyed 경고 방지)
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


save_as_spe = _spe_writer_save_spe


def acquire_images(
    cam_or_wrapper,
    nframes: int,
    timeout_s: float = 10.0,
    progress_cb: Optional[Callable[[int, int], None]] = None,
):
    """이미지를 여러 장 취득해서 리스트로 반환한다."""
    wrapper = _as_wrapper(cam_or_wrapper)
    return wrapper.acquire_images(nframes, timeout_s=timeout_s, progress_cb=progress_cb)


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
