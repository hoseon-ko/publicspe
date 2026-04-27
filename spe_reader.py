"""
spe_reader.py
SPE 2.x / 3.0 파일 리더 (외부 라이브러리 불필요, numpy만 사용)
Princeton Instruments / Teledyne LightField 포맷 지원

주요 기능
---------
- SpeFile         : SPE 파일 읽기 클래스
- extract_xml     : XML footer 추출 및 저장 (SPE 3.0)
- save_pgm        : 프레임을 16-bit PGM P5 파일로 저장

사용 예시
---------
    from spe_reader import SpeFile, extract_xml, save_pgm

    spe = SpeFile("data.spe")
    print(spe)                   # 파일 요약
    print(spe.meta)              # 메타데이터 딕셔너리
    data = spe.data              # numpy 배열 (frames, height, width)
    spec = spe.spectrum()        # 스펙트럼 (1D)

    extract_xml(spe)             # data.xml 저장
    save_pgm(spe)                # data.pgm 저장 (프레임 여러 개면 _frame0.pgm ...)
"""

import struct
from typing import Union
import numpy as np
from pathlib import Path


# ── 데이터 타입 매핑 (SPE 스펙 Table 4) ──────────────────────────────────────
_DTYPE_MAP = {
    0: np.float32,   # 32f
    1: np.int32,     # 32s
    2: np.int16,     # 16s
    3: np.uint16,    # 16u
    8: np.uint32,    # 32u
}

_PIXFMT_MAP = {
    "MonochromeUnsigned16": np.uint16,
    "MonochromeUnsigned32": np.uint32,
    "MonochromeFloating32": np.float32,
}

HEADER_SIZE = 4100  # 바이트, SPE 2.x / 3.0 공통 고정값


# ─────────────────────────────────────────────────────────────────────────────
# SpeFile 클래스
# ─────────────────────────────────────────────────────────────────────────────

class SpeFile:
    """
    SPE 2.x / 3.0 파일을 읽는 클래스.

    Attributes
    ----------
    path : Path
    version : float
        SPE 버전 (예: 2.0, 3.0)
    meta : dict
        실험 메타데이터
    data : np.ndarray
        shape = (num_frames, height, width)
    """

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            self._raw = f.read()

        self.version: float = self._read_version()
        self._xml: str | None = None

        if self.version >= 3.0:
            self._xml = self._read_xml_footer()

        self.meta: dict = self._parse_meta()
        self.data: np.ndarray = self._read_data()

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _u(self, fmt: str, offset: int):
        """헤더에서 단일 값을 언팩."""
        return struct.unpack_from(fmt, self._raw, offset)[0]

    def _read_version(self) -> float:
        ver = self._u("<f", 1992)   # file_header_ver @ offset 1992
        return round(ver, 1) if ver > 0 else 2.0

    def _read_xml_footer(self) -> str:
        xml_offset = self._u("<Q", 678)  # xml_footer_offset @ offset 678 (64u)
        if xml_offset == 0 or xml_offset >= len(self._raw):
            return ""
        return self._raw[xml_offset:].decode("utf-8", errors="replace")

    # ── 메타데이터 ────────────────────────────────────────────────────────────

    def _parse_meta(self) -> dict:
        meta = {}
        meta["version"] = self.version
        meta["file"] = str(self.path)

        meta["xdim"]       = self._u("<H", 42)
        meta["ydim"]       = self._u("<H", 656)
        meta["datatype"]   = self._u("<h", 108)
        meta["num_frames"] = self._u("<i", 1446)

        if self.version >= 3.0 and self._xml:
            meta.update(self._parse_xml_meta())
        else:
            meta.update(self._parse_header_meta())

        return meta

    def _parse_header_meta(self) -> dict:
        """SPE 2.x 헤더에서 실험 정보 추출."""
        m = {}
        m["exposure_sec"]          = self._u("<f", 10)
        m["detector_temperature"]  = self._u("<f", 36)
        m["accumulations"]         = self._u("<i", 668)
        m["center_wavelength_nm"]  = self._u("<f", 72)

        raw_date    = self._raw[20:30].rstrip(b"\x00")
        raw_time    = self._raw[172:178].rstrip(b"\x00")
        raw_comment = self._raw[200:600].rstrip(b"\x00")

        m["date"]     = raw_date.decode("latin-1", errors="replace").strip()
        m["time"]     = raw_time.decode("latin-1", errors="replace").strip()
        m["comments"] = raw_comment.decode("latin-1", errors="replace").strip()

        # X축 파장 보정 폴리노미알 계수 6개 (64f × 6 @ offset 3263)
        coeffs = struct.unpack_from("<6d", self._raw, 3263)
        m["wavelength_poly_coeffs"] = list(coeffs)

        return m

    def _parse_xml_meta(self) -> dict:
        """SPE 3.0 XML Footer에서 실험 정보 추출."""
        import re
        xml = self._xml
        m = {}

        def first(pattern):
            hits = re.findall(pattern, xml)
            return hits[0].strip() if hits else None

        m["created"]                = first(r'created="([^"]+)"')
        m["software"]               = first(r'software="([^"]+)"')
        m["software_version"]       = first(r'softwareVersion="([^"]+)"')
        m["camera_model"]           = first(r'model="([^"]+)"')
        m["camera_serial"]          = first(r'serialNumber="([^"]+)"')
        m["camera_interface"]       = first(r'computerInterface="([^"]+)"')
        m["sensor_name"]            = first(r'<SensorName[^>]*>([^<]+)')
        m["sensor_characteristics"] = first(r'<CcdCharacteristics[^>]*>([^<]+)')

        px = re.findall(
            r'<Pixel>.*?<Width[^>]*>([\d.]+).*?<Height[^>]*>([\d.]+)',
            xml, re.DOTALL
        )
        if px:
            m["pixel_size_um"] = (float(px[0][0]), float(px[0][1]))

        exp  = first(r'ExposureTime type="Double">([\d.]+)')
        unit = first(r'<TimeUnit[^>]*>([^<]+)')
        if exp:
            m["exposure_time"]      = float(exp)
            m["exposure_time_unit"] = unit or "unknown"

        m["adc_quality"]      = first(r'<Quality[^>]*>([^<]+)')
        rr = first(r'<ReadoutRate[^>]*>([\d.eE+\-]+)')
        m["readout_rate_mhz"] = float(rr) if rr else None
        m["analog_gain"]      = first(r'<AnalogGain[^>]*>([^<]+)')
        bd = first(r'<BitDepth[^>]*>(\d+)')
        m["bit_depth"]        = int(bd) if bd else None

        xb = first(r'<XBinning[^>]*>(\d+)')
        yb = first(r'<YBinning[^>]*>(\d+)')
        m["binning"] = (int(xb), int(yb)) if xb and yb else None

        sp = first(r'<SetPoint[^>]*>([\d.\-]+)')
        rd = first(r'<Reading r:readOnly[^>]*>([\d.\-]+)')
        st = first(r'<SensorTemperature[^>]*>([^<]+)')
        if sp:
            m["temperature_setpoint_c"] = float(sp)
        if rd:
            m["temperature_reading_c"]  = float(rd)
        m["temperature_status"] = st

        m["shutter_timing"]   = first(r'<ShutterTiming[^>]*>([^<]+)')
        m["trigger_response"] = first(r'<TriggerResponse[^>]*>([^<]+)')

        roi_tags = re.findall(r'<DataBlock type="Region"[^/]*/>', xml)
        rois = []
        for tag in roi_tags:
            roi = {}
            for attr in ("width", "height", "size", "stride", "calibrations"):
                v = re.search(rf'{attr}="([^"]+)"', tag)
                if v:
                    roi[attr] = v.group(1)
            rois.append(roi)
        m["rois"] = rois

        wl_raw = re.findall(
            r'<Wavelength[^>]*>(.*?)</Wavelength>', xml, re.DOTALL
        )
        if wl_raw:
            vals = [
                float(v)
                for v in wl_raw[0].replace("\n", "").split(",")
                if v.strip()
            ]
            m["wavelengths_nm"] = np.array(vals)

        return m

    # ── 이미지 데이터 ─────────────────────────────────────────────────────────

    def _read_data(self) -> np.ndarray:
        xdim       = self.meta["xdim"]
        ydim       = self.meta["ydim"]
        num_frames = max(self.meta["num_frames"], 1)

        if self.version >= 3.0 and self._xml:
            import re
            pf    = re.search(r'pixelFormat="([^"]+)"', self._xml)
            dtype = _PIXFMT_MAP.get(pf.group(1), np.uint16) if pf else np.uint16
        else:
            dtype = _DTYPE_MAP.get(self.meta["datatype"], np.uint16)

        itemsize    = np.dtype(dtype).itemsize
        frame_bytes = xdim * ydim * itemsize
        stride      = self._frame_stride() or frame_bytes

        frames = []
        offset = HEADER_SIZE
        for _ in range(num_frames):
            if offset + frame_bytes > len(self._raw):
                break
            frame = np.frombuffer(
                self._raw[offset : offset + frame_bytes], dtype=dtype
            ).reshape(ydim, xdim)
            frames.append(frame.copy())
            offset += stride

        if not frames:
            raise ValueError("이미지 데이터를 읽지 못했습니다.")

        return np.stack(frames, axis=0)  # (frames, height, width)

    def _frame_stride(self) -> int:
        """XML Footer에서 Frame stride 값을 읽는다."""
        if not self._xml:
            return 0
        import re
        m = re.search(r'<DataBlock type="Frame"[^>]*stride="(\d+)"', self._xml)
        return int(m.group(1)) if m else 0

    # ── 편의 프로퍼티 / 메서드 ────────────────────────────────────────────────

    @property
    def shape(self) -> tuple:
        """(num_frames, height, width)"""
        return self.data.shape

    @property
    def num_frames(self) -> int:
        return self.data.shape[0]

    @property
    def wavelengths(self) -> "np.ndarray | None":
        """파장 캘리브레이션 배열 (nm). 없으면 None."""
        return self.meta.get("wavelengths_nm")

    def frame(self, idx: int = 0) -> np.ndarray:
        """특정 프레임을 2D 배열 (height × width)로 반환."""
        return self.data[idx]

    def _roi_slices(
        self,
        roi: "tuple[int, int, int, int] | None",
        shape: tuple,
    ) -> tuple:
        """roi=(x0, x1, y0, y1) → numpy slice 쌍 (y, x)."""
        h, w = shape
        if roi is None:
            return slice(0, h), slice(0, w)

        x0, x1, y0, y1 = roi
        x0 = max(0, min(int(x0), w))
        x1 = max(0, min(int(x1), w))
        y0 = max(0, min(int(y0), h))
        y1 = max(0, min(int(y1), h))

        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"잘못된 ROI입니다: {roi}")

        return slice(y0, y1), slice(x0, x1)

    def spectrum(
        self,
        row_center: "int | None" = None,
        half_width: int = 10,
        frame_idx: int = 0,
    ) -> np.ndarray:
        """
        지정 행 주변 ±half_width 행의 평균으로 스펙트럼 (1D) 반환.

        Parameters
        ----------
        row_center : int | None
            기준 행 인덱스. None이면 평균 강도가 가장 높은 행 자동 선택.
        half_width : int
            평균낼 행 수 (위아래).
        frame_idx : int
            프레임 인덱스.
        """
        img = self.data[frame_idx]
        if row_center is None:
            row_center = int(np.argmax(img.mean(axis=1)))
        r0 = max(0, row_center - half_width)
        r1 = min(img.shape[0], row_center + half_width + 1)
        return img[r0:r1, :].mean(axis=0)

    def fft2d(
        self,
        frame_idx: int = 0,
        shift: bool = True,
        log_scale: bool = True,
        eps: float = 1e-12,
        roi: "tuple[int, int, int, int] | None" = None,
        remove_mean: bool = True,
        window: "str | None" = "hann",
        normalize: bool = True,
    ) -> np.ndarray:
        """
        특정 프레임의 2D FFT 크기 스펙트럼 반환.

        Parameters
        ----------
        frame_idx : int
        shift : bool        True면 0 주파수를 중앙으로 이동 (fftshift).
        log_scale : bool    True면 log10 스케일로 반환.
        eps : float         log 계산 안정화용.
        roi : tuple | None  (x0, x1, y0, y1). 지정 시 해당 ROI만 FFT.
        remove_mean : bool  True면 FFT 전에 DC 오프셋 제거.
        window : str | None "hann" 또는 None.
        normalize : bool    True면 픽셀 수로 나눠 정규화.
        """
        img = self.data[frame_idx].astype(np.float64, copy=False)
        ys, xs = self._roi_slices(roi, img.shape)
        img = img[ys, xs].copy()

        if remove_mean:
            img -= img.mean()

        if window == "hann":
            wy = np.hanning(img.shape[0])
            wx = np.hanning(img.shape[1])
            img *= np.outer(wy, wx)
        elif window is not None:
            raise ValueError(f"지원하지 않는 window: {window}")

        f = np.fft.fft2(img)
        if normalize:
            n = img.shape[0] * img.shape[1]
            if n > 0:
                f = f / n
        if shift:
            f = np.fft.fftshift(f)
        mag = np.abs(f)
        if log_scale:
            mag = np.log10(mag + eps)
        return mag

    def remove_dc(
        self,
        frame_idx: int = 0,
        via_fft: bool = True,
        roi: "tuple[int, int, int, int] | None" = None,
        return_full: bool = False,
    ) -> np.ndarray:
        """
        특정 프레임에서 DC (평균 / 0주파수) 성분을 제거한 이미지 반환.

        Parameters
        ----------
        frame_idx : int
        via_fft : bool      True면 FFT DC bin=0 후 역변환. False면 평균 직접 차감.
        roi : tuple | None  (x0, x1, y0, y1).
        return_full : bool  True면 원본 크기로 반환 (ROI 외 영역은 원본 유지).
        """
        img_full = self.data[frame_idx].astype(np.float64, copy=False)
        ys, xs = self._roi_slices(roi, img_full.shape)
        img = img_full[ys, xs]

        if not via_fft:
            out = img - img.mean()
        else:
            f = np.fft.fft2(img)
            f[0, 0] = 0
            out = np.fft.ifft2(f).real

        if return_full:
            merged = img_full.copy()
            merged[ys, xs] = out
            return merged

        return out

    def __repr__(self) -> str:
        m = self.meta
        lines = [
            f"SpeFile('{self.path.name}')",
            f"  version      : {self.version}",
            f"  shape        : {self.shape}  (frames × height × width)",
            f"  dtype        : {self.data.dtype}",
        ]
        if m.get("camera_model"):
            lines.append(f"  camera       : {m['camera_model']}")
        if m.get("exposure_time") is not None:
            lines.append(
                f"  exposure     : {m['exposure_time']} {m.get('exposure_time_unit', '')}"
            )
        if m.get("exposure_sec"):
            lines.append(f"  exposure     : {m['exposure_sec']} sec")
        if m.get("created"):
            lines.append(f"  created      : {m['created']}")
        if m.get("temperature_setpoint_c") is not None:
            lines.append(
                f"  temperature  : {m['temperature_setpoint_c']} °C"
                f"  ({m.get('temperature_status', '')})"
            )
        if m.get("binning"):
            lines.append(f"  binning      : {m['binning'][0]}×{m['binning'][1]}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# XML footer 추출
# ─────────────────────────────────────────────────────────────────────────────

def extract_xml(spe: SpeFile, out_path: "str | Path | None" = None) -> "Path | None":
    """
    SPE 3.0 파일의 XML footer를 .xml 파일로 저장한다.

    Parameters
    ----------
    spe : SpeFile
    out_path : str | Path | None
        저장 경로. None이면 SPE 파일과 같은 폴더에 같은 이름으로 저장.

    Returns
    -------
    Path | None
        저장된 파일 경로. XML이 없으면 None 반환.
    """
    if not spe._xml:
        print(f"[extract_xml] XML footer 없음 (SPE {spe.version} — SPE 2.x는 XML 미지원)")
        return None

    dst = Path(out_path) if out_path else spe.path.with_suffix(".xml")
    dst.write_text(spe._xml, encoding="utf-8")
    print(f"[extract_xml] 저장: {dst}")
    return dst


# ─────────────────────────────────────────────────────────────────────────────
# PGM P5 저장
# ─────────────────────────────────────────────────────────────────────────────

def save_pgm(
    spe: SpeFile,
    out_dir: "str | Path | None" = None,
    frames: "list[int] | None" = None,
) -> list:
    """
    SPE 프레임을 16-bit binary PGM (P5) 파일로 저장한다.

    Parameters
    ----------
    spe : SpeFile
    out_dir : str | Path | None
        저장 폴더. None이면 SPE 파일과 같은 폴더.
    frames : list[int] | None
        저장할 프레임 인덱스 목록. None이면 전체 프레임 저장.

    Returns
    -------
    list[Path]
        저장된 파일 경로 목록.
    """
    dst_dir = Path(out_dir) if out_dir else spe.path.parent
    stem    = spe.path.stem
    indices = frames if frames is not None else list(range(spe.num_frames))
    multi   = len(indices) > 1
    saved   = []

    for i in indices:
        suffix   = f"_frame{i}" if multi else ""
        out_path = dst_dir / f"{stem}{suffix}.pgm"

        frame = spe.frame(i)
        h, w  = frame.shape

        # uint16이 아니면 0–65535 범위로 선형 정규화
        if frame.dtype != np.uint16:
            vmin_f, vmax_f = float(frame.min()), float(frame.max())
            if vmax_f > vmin_f:
                frame_u16 = (
                    (frame - vmin_f) / (vmax_f - vmin_f) * 65535
                ).astype(np.uint16)
            else:
                frame_u16 = np.zeros_like(frame, dtype=np.uint16)
        else:
            frame_u16 = frame

        header     = f"P5\n{w} {h}\n65535\n".encode("ascii")
        pixel_data = frame_u16.astype(">u2").tobytes()  # big-endian uint16

        with open(out_path, "wb") as f:
            f.write(header + pixel_data)

        print(f"[save_pgm] 저장: {out_path}  ({w}×{h} px, 16-bit P5 PGM)")
        saved.append(out_path)

    return saved
