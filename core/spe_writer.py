"""
core/spe_writer.py
SPE 3.0 포맷 파일 저장 — LightField 호환 XML 구조.
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


def _xml_tag(tag: str, value: Any, readonly: bool = False) -> str:
    if value is None:
        return ""
    text = str(value)
    if text == "":
        return ""
    ro = ' r:readOnly="true"' if readonly else ""
    return "<" + tag + ro + ">" + escape(text) + "</" + tag + ">"


def save_spe(
    path,
    frames,
    *,
    exposure_ms: float = 0.0,
    roi=None,
    dtype=None,
    # 카메라 기본 정보
    camera_name: str = "Camera1",
    camera_model: str = "",
    camera_serial: str = "",
    camera_interface: str = "",
    # 픽셀 크기
    pixel_size_um: Optional[tuple] = None,
    # 센서 정보
    sensor_name: Optional[str] = None,
    sensor_type: Optional[str] = None,
    sensor_characteristics: Optional[str] = None,
    # 온도
    temperature_reading_c: Optional[float] = None,
    temperature_setpoint_c: Optional[float] = None,
    temperature_status: Optional[str] = None,
    # ShutterTiming 확장
    shutter_mode: Optional[str] = None,
    shutter_opening_delay_ms: Optional[float] = None,
    shutter_closing_delay_ms: Optional[float] = None,
    # ReadoutControl 확장
    readout_mode: Optional[str] = None,
    readout_ports_used: Optional[int] = None,
    vertical_shift_rate: Optional[float] = None,
    # ADC
    adc_info: Optional[Dict[str, Any]] = None,
    readout_rate_mhz: Optional[float] = None,
    # 소프트웨어 / 파일 메타
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
    path              : 저장 경로 (.spe 권장)
    frames            : ndarray (H,W) / (N,H,W) / list of ndarray
    exposure_ms       : 노출 시간 (ms)
    roi               : (hstart, hend, vstart, vend, hbin, vbin)
    sensor_name       : 예) "E2V 2048 x 2048 (CCD 42-40)(B)(MP)"
    sensor_type       : 예) "Ccd"
    sensor_characteristics : 예) "BackIlluminated, Multiport"
    readout_mode      : 예) "FullFrame"
    vertical_shift_rate : µs 단위 shift rate
    shutter_mode      : 예) "Normal"
    adc_info          : {adc_quality, adc_speed, adc_analog_gain, bit_depth, readout_ports_used}
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

    # ── ROI ───────────────────────────────────────────────────────────
    x0, y0, hbin, vbin = 0, 0, 1, 1
    if roi is not None:
        x0   = int(roi[0])
        y0   = int(roi[2])
        hbin = int(roi[4]) if len(roi) > 4 else 1
        vbin = int(roi[5]) if len(roi) > 5 else 1

    # ── 헤더 (4100 bytes) ─────────────────────────────────────────────
    header = bytearray(4100)
    footer_offset = 4100 + nframes * frame_bytes
    struct.pack_into("<H", header, 42,   min(width,  65535))
    struct.pack_into("<H", header, 656,  min(height, 65535))
    struct.pack_into("<h", header, 108,  dtype_code)
    struct.pack_into("<i", header, 1446, nframes)
    struct.pack_into("<f", header, 1992, 3.0)
    struct.pack_into("<Q", header, 678,  footer_offset)

    created_str = created or datetime.now().astimezone().isoformat()
    cam_name  = camera_name or "Camera1"
    cam_model = camera_model or cam_name

    # ── readout_ports_used: adc_info와 별도 파라미터 중 우선순위 ──────
    _ports = readout_ports_used
    if _ports is None and adc_info:
        _ports = adc_info.get("readout_ports_used")

    # ── Sensor Information XML ────────────────────────────────────────
    sensor_info_xml = ""
    has_sensor_info = any(v is not None for v in (
        sensor_name, sensor_type, sensor_characteristics, pixel_size_um
    ))
    if has_sensor_info:
        pixel_info_xml = ""
        if isinstance(pixel_size_um, (tuple, list)) and len(pixel_size_um) >= 2:
            pw_str = "{:.6f}".format(float(pixel_size_um[0]))
            ph_str = "{:.6f}".format(float(pixel_size_um[1]))
            pixel_info_xml = (
                "<Pixel>"
                '<Width r:readOnly="true">' + pw_str + "</Width>"
                '<Height r:readOnly="true">' + ph_str + "</Height>"
                "</Pixel>"
            )
        sensor_info_xml = (
            "<Information>"
            + _xml_tag("SensorName", sensor_name, readonly=True)
            + _xml_tag("CcdCharacteristics", sensor_characteristics, readonly=True)
            + _xml_tag("Type", sensor_type, readonly=True)
            + pixel_info_xml
            + "</Information>"
        )

    # ── Sensor Temperature XML ────────────────────────────────────────
    temp_inner_xml = ""
    if any(v is not None for v in (temperature_reading_c, temperature_setpoint_c, temperature_status)):
        reading_xml = (
            '<Reading r:readOnly="true">{:.4f}</Reading>'.format(float(temperature_reading_c))
            if temperature_reading_c is not None else ""
        )
        temp_inner_xml = (
            "<Temperature>"
            + _xml_tag("SetPoint", temperature_setpoint_c)
            + reading_xml
            + _xml_tag("Status", temperature_status, readonly=True)
            + "</Temperature>"
        )

    sensor_xml = ""
    if sensor_info_xml or temp_inner_xml:
        sensor_xml = f"<Sensor>{sensor_info_xml}{temp_inner_xml}</Sensor>"

    # ── ShutterTiming XML ─────────────────────────────────────────────
    shutter_xml = (
        "<ShutterTiming>"
        + '<ExposureTime type="Double">{:.6f}</ExposureTime>'.format(float(exposure_ms))
        + "<TimeUnit>ms</TimeUnit>"
        + _xml_tag("Mode", shutter_mode)
        + _xml_tag("OpeningDelay", shutter_opening_delay_ms)
        + _xml_tag("ClosingDelay", shutter_closing_delay_ms)
        + "</ShutterTiming>"
    )

    # ── ReadoutControl XML ────────────────────────────────────────────
    roi_regions_xml = (
        "<RegionsOfInterest><CustomRegions>"
        "<RegionOfInterest>"
        f"<X>{x0}</X><Y>{y0}</Y><Width>{width}</Width><Height>{height}</Height>"
        f"<XBinning>{hbin}</XBinning><YBinning>{vbin}</YBinning>"
        "</RegionOfInterest>"
        "</CustomRegions></RegionsOfInterest>"
    )
    readout_xml = (
        "<ReadoutControl>"
        + _xml_tag("Mode", readout_mode)
        + _xml_tag("PortsUsed", _ports)
        + _xml_tag("VerticalShiftRate", vertical_shift_rate)
        + roi_regions_xml
        + "</ReadoutControl>"
    )

    # ── Adc XML ───────────────────────────────────────────────────────
    adc_xml = ""
    if adc_info:
        adc_xml = (
            "<Adc>"
            + _xml_tag("Speed", adc_info.get("adc_speed"))
            + _xml_tag("BitDepth", adc_info.get("bit_depth"), readonly=True)
            + _xml_tag("AnalogGain", adc_info.get("adc_analog_gain"))
            + _xml_tag("Quality", adc_info.get("adc_quality"))
            + _xml_tag("ReadoutRate", readout_rate_mhz, readonly=True)
            + "</Adc>"
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

    # ── Origin 속성 ───────────────────────────────────────────────────
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
        f"{sensor_xml}"
        f"{shutter_xml}"
        f"{readout_xml}"
        f"{adc_xml}"
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
