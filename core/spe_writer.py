"""
core/spe_writer.py
SPE 3.0 포맷 파일 저장 — picamp.py의 _save_as_spe를 독립 모듈로 분리.
HIKVISION, Picam 등 모든 카메라 데이터를 동일하게 저장할 수 있다.
"""

import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

import numpy as np

_SPE_DTYPE_CODE: Dict[str, int] = {
    "float32": 0,
    "int32":   1,
    "int16":   2,
    "uint16":  3,
    "uint32":  8,
}

_SPE_PIXEL_FORMAT: Dict[str, str] = {
    "float32": "MonochromeFloating32",
    "uint16":  "MonochromeUnsigned16",
    "uint32":  "MonochromeUnsigned32",
}


def _xml_tag(tag: str, value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text == "":
        return ""
    return f"<{tag}>{escape(text)}</{tag}>"


def save_spe(
    path,
    frames,
    *,
    exposure_ms: float = 0.0,
    roi=None,
    dtype=None,
    camera_name: str = "Camera1",
    camera_model: str = "",
    camera_serial: str = "",
    camera_interface: str = "",
    pixel_size_um: Optional[tuple] = None,
    temperature_reading_c: Optional[float] = None,
    temperature_setpoint_c: Optional[float] = None,
    temperature_status: Optional[str] = None,
    sensor_name: Optional[str] = None,
    sensor_characteristics: Optional[str] = None,
    adc_info: Optional[Dict[str, Any]] = None,
    readout_rate_mhz: Optional[float] = None,
    software: str = "SpeAnalyze",
    software_version: str = "1.0",
    software_company: str = "",
    creator: str = "",
    created: Optional[str] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    SPE 3.0 포맷으로 이미지를 저장한다 (LightField / spe_loader 호환).

    Parameters
    ----------
    path         : 저장 경로 (str 또는 Path), .spe 확장자 권장
    frames       : ndarray (H,W) 또는 (N,H,W), 또는 list of ndarray
    exposure_ms  : 노출 시간 ms — 메타데이터에 기록
    roi          : (hstart, hend, vstart, vend, hbin, vbin) — pilablib roi 반환값
    dtype        : 저장 dtype (None = 원본 유지)
    camera_name  : XML 푸터에 기록할 카메라 이름
    pixel_size_um: (width_um, height_um) 픽셀 크기

    Returns
    -------
    저장된 파일의 Path
    """
    # ── frames 통일 ────────────────────────────────────────────────────
    if isinstance(frames, np.ndarray):
        frames = [frames[i] for i in range(frames.shape[0])] if frames.ndim == 3 else [frames]
    frames = list(frames)
    if not frames:
        raise ValueError("frames is empty")

    frame0 = np.asarray(frames[0])
    height, width = frame0.shape

    out_dtype = np.dtype(dtype) if dtype is not None else frame0.dtype
    dtype_code = _SPE_DTYPE_CODE.get(out_dtype.name, 3)
    if out_dtype.name not in _SPE_DTYPE_CODE:
        out_dtype = np.dtype("uint16")
        dtype_code = 3
    pixel_format = _SPE_PIXEL_FORMAT.get(out_dtype.name, "MonochromeUnsigned16")

    nframes = len(frames)
    bytes_per_px = out_dtype.itemsize
    frame_bytes = width * height * bytes_per_px

    # ── ROI 파라미터 ───────────────────────────────────────────────────
    x0, y0, hbin, vbin = 0, 0, 1, 1
    if roi is not None:
        x0   = int(roi[0])
        y0   = int(roi[2])
        hbin = int(roi[4]) if len(roi) > 4 else 1
        vbin = int(roi[5]) if len(roi) > 5 else 1

    # ── 헤더 (4100 bytes) ──────────────────────────────────────────────
    header = bytearray(4100)
    footer_offset = 4100 + nframes * frame_bytes
    struct.pack_into("<H", header, 42,   min(width,  65535))
    struct.pack_into("<H", header, 656,  min(height, 65535))
    struct.pack_into("<h", header, 108,  dtype_code)
    struct.pack_into("<i", header, 1446, nframes)
    struct.pack_into("<f", header, 1992, 3.0)          # SPE version
    struct.pack_into("<Q", header, 678,  footer_offset)

    created_str = created or datetime.now().astimezone().isoformat()
    cam_name = camera_name or "Camera1"
    cam_model = camera_model or cam_name

    # ── 픽셀 크기 XML ──────────────────────────────────────────────────
    pixel_xml = ""
    if isinstance(pixel_size_um, (tuple, list)) and len(pixel_size_um) >= 2:
        pixel_xml = (
            "<Pixel>"
            f"<Width>{float(pixel_size_um[0]):.6f}</Width>"
            f"<Height>{float(pixel_size_um[1]):.6f}</Height>"
            "</Pixel>"
        )

    # ── 온도 XML ───────────────────────────────────────────────────────
    temp_xml = ""
    if any(v is not None for v in (temperature_reading_c, temperature_setpoint_c, temperature_status)):
        reading_xml = (
            f'<Reading r:readOnly="true">{float(temperature_reading_c):.4f}</Reading>'
            if temperature_reading_c is not None else ""
        )
        temp_xml = (
            "<Sensor>"
            f"{_xml_tag('SensorName', sensor_name)}"
            f"{_xml_tag('CcdCharacteristics', sensor_characteristics)}"
            "<Temperature>"
            f"{_xml_tag('SetPoint', temperature_setpoint_c)}"
            f"{reading_xml}"
            f"{_xml_tag('SensorTemperature', temperature_status)}"
            "</Temperature>"
            "</Sensor>"
        )

    # ── ADC XML ────────────────────────────────────────────────────────
    adc_xml = ""
    if adc_info:
        adc_xml = (
            "<ADC>"
            f"{_xml_tag('Quality', adc_info.get('adc_quality'))}"
            f"{_xml_tag('Speed', adc_info.get('adc_speed'))}"
            f"{_xml_tag('ReadoutRate', readout_rate_mhz)}"
            f"{_xml_tag('AnalogGain', adc_info.get('adc_analog_gain'))}"
            f"{_xml_tag('BitDepth', adc_info.get('bit_depth'))}"
            f"{_xml_tag('ReadoutPortsUsed', adc_info.get('readout_ports_used'))}"
            "</ADC>"
        )

    roi_xml = (
        "<ReadoutControl><RegionsOfInterest><CustomRegions>"
        "<RegionOfInterest>"
        f"<X>{x0}</X><Y>{y0}</Y><Width>{width}</Width><Height>{height}</Height>"
        f"<XBinning>{hbin}</XBinning><YBinning>{vbin}</YBinning>"
        "</RegionOfInterest>"
        "</CustomRegions></RegionsOfInterest></ReadoutControl>"
    )

    exposure_xml = (
        "<ShutterTiming>"
        f'<ExposureTime type="Double">{float(exposure_ms):.6f}</ExposureTime>'
        "<TimeUnit>ms</TimeUnit>"
        "</ShutterTiming>"
    )

    # ── Calibrations XML ──────────────────────────────────────────────
    calibrations_xml = (
        "<Calibrations>"
        f'<SensorInformation id="1" orientation="Normal" width="{width}" height="{height}" />'
        f'<SensorMapping id="2" x="{x0}" y="{y0}" width="{width}" height="{height}"'
        f' xBinning="{hbin}" yBinning="{vbin}" />'
        "</Calibrations>"
    )

    # ── GeneralInformation XML ────────────────────────────────────────
    creator_attr = f' creator="{escape(creator)}"' if creator else ""

    # extra_metadata → <CustomData> 섹션
    custom_xml = ""
    if extra_metadata:
        def _dict_to_xml(d: dict) -> str:
            parts = []
            for k, v in d.items():
                tag = str(k).replace(" ", "_")
                if isinstance(v, dict):
                    parts.append(f"<{tag}>{_dict_to_xml(v)}</{tag}>")
                else:
                    parts.append(f"<{tag}>{escape(str(v))}</{tag}>")
            return "".join(parts)
        custom_xml = f"<CustomData>{_dict_to_xml(extra_metadata)}</CustomData>"

    general_info_xml = (
        "<GeneralInformation>"
        f'<FileInformation{creator_attr} created="{escape(created_str)}"'
        f' lastModified="{escape(created_str)}" />'
        f"{custom_xml}"
        "</GeneralInformation>"
    )

    # ── Origin attributes ─────────────────────────────────────────────
    origin_creator_attr = f' creator="{escape(creator)}"' if creator else ""
    origin_company_attr = f' softwareCompany="{escape(software_company)}"' if software_company else ""

    xml_footer = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<SpeFormat version="3.0" xmlns="http://www.princetoninstruments.com/spe/2009">'
        "<DataFormat>"
        f'<DataBlock type="Frame" count="{nframes}" size="{frame_bytes}" stride="{frame_bytes}"'
        f' pixelFormat="{pixel_format}">'
        f'<DataBlock type="Region" count="1" size="{frame_bytes}" stride="{frame_bytes}"'
        f' width="{width}" height="{height}" calibrations="1,2"/>'
        "</DataBlock>"
        "</DataFormat>"
        f"{calibrations_xml}"
        '<DataHistories><DataHistory id="1"><Origin'
        f'{origin_creator_attr}'
        f' created="{escape(created_str)}"'
        f' software="{escape(software)}"'
        f' softwareVersion="{escape(software_version)}"'
        f'{origin_company_attr}>'
        '<Experiment xmlns="http://www.princetoninstruments.com/experiment/2009"'
        ' xmlns:r="http://www.princetoninstruments.com/experiment/restore/2009">'
        "<Devices><Cameras>"
        f'<Camera name="{escape(cam_name)}"'
        f' model="{escape(cam_model)}"'
        f' serialNumber="{escape(camera_serial)}"'
        f' computerInterface="{escape(camera_interface)}">'
        f"{exposure_xml}"
        f"{roi_xml}"
        f"{pixel_xml}"
        f"{adc_xml}"
        f"{temp_xml}"
        "</Camera>"
        "</Cameras></Devices></Experiment>"
        "</Origin></DataHistory></DataHistories>"
        f"{general_info_xml}"
        "</SpeFormat>"
    )

    # ── 파일 쓰기 ─────────────────────────────────────────────────────
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(bytes(header))
        for frame in frames:
            f.write(np.asarray(frame, dtype=out_dtype).tobytes())
        f.write(xml_footer.encode("utf-8"))

    return out_path
