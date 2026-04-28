"""
core/image_processor.py
카메라 종류와 무관한 소프트웨어 후처리 파이프라인.

raw frame (numpy ndarray, uint8 or uint16) →
ImageProcessor.process() →
ProcessedFrame(raw, display, fps, centroid_x, centroid_y, ...)

개선 이력 (v2):
  P0-1  내부 파이프라인 float32 유지 → uint16 다이나믹 레인지 보존
  P0-2  DisplayStretch 모드 (NORMALIZE / PERCENTILE / MANUAL)
  P1-1  공간 필터 (핫픽셀 제거 / Gaussian blur / Median filter)
  P2-1  Dark frame + Flat field 보정
  P2-2  Intensity 가중 Centroid (Weighted 모드)
  P2-3  기본 통계 (mean, std, max, SNR 추정) ProcessedFrame 포함
  P2-4  TemporalMode (AVERAGE / MAX_PROJ / MIN_PROJ / STD_MAP / ACCUM)
  P3-3  포화 픽셀 감지 (sat_ratio, saturated 플래그)
  P3-4  프레임 차분 이미지 (temporal_diff_enabled)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import numpy as np

# cv2 는 선택적 의존성
try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

# ── 이진화 모드 상수 (cv2 없어도 사용 가능) ──────────────────────────────────

BIN_BINARY     = 0   # cv2.THRESH_BINARY
BIN_BINARY_INV = 1   # cv2.THRESH_BINARY_INV
BIN_TOZERO     = 2   # cv2.THRESH_TOZERO
BIN_TOZERO_INV = 3   # cv2.THRESH_TOZERO_INV

_BIN_MODE_MAP = {
    BIN_BINARY:     0,
    BIN_BINARY_INV: 1,
    BIN_TOZERO:     2,
    BIN_TOZERO_INV: 3,
}


# ── 열거형 ────────────────────────────────────────────────────────────────────

class DisplayStretch(IntEnum):
    """Display 이미지 스트레칭 방식."""
    NORMALIZE  = 0   # min-max 자동 스트레치 (기본값)
    PERCENTILE = 1   # 하위/상위 N% 클리핑 후 스트레치
    MANUAL     = 2   # display_min / display_max 수동 지정


class TemporalMode(IntEnum):
    """시간 축 N-frame 연산 모드."""
    AVERAGE  = 0   # N-frame 평균 (기본값)
    MAX_PROJ = 1   # 최대값 투영 — 희미한 이벤트 누적에 유리
    MIN_PROJ = 2   # 최소값 투영 — 이동 물체 제거
    STD_MAP  = 3   # 표준편차 맵 — 공간적 노이즈 시각화
    ACCUM    = 4   # 무한 누적 — 광자 카운팅 모드


class CentroidMode(IntEnum):
    """Centroid 계산 방식."""
    BINARY       = 0   # 이진화 이미지 moments (임계값에 민감)
    WEIGHTED     = 1   # intensity 가중 centroid (더 정확)
    GAUSSIAN_FIT = 2   # 2D Gaussian 피팅 — sub-pixel 정밀도 (scipy 필요)
    PEAK_MAX     = 3   # 최대값 위치 — 빠름, 노이즈에 약함


# ── 결과 데이터 클래스 ────────────────────────────────────────────────────────

@dataclass
class ProcessedFrame:
    raw:          np.ndarray              # 원본 프레임 (원본 dtype 그대로)
    display:      np.ndarray              # 화면 표시용 uint8
    fps:          float = 0.0

    # Centroid
    centroid_x:   Optional[float] = None
    centroid_y:   Optional[float] = None
    brightness:   int   = 0
    has_centroid: bool  = False
    fit_sigma_x:  float = 0.0   # Gaussian fit X 폭 (pixel), GAUSSIAN_FIT 모드 전용
    fit_sigma_y:  float = 0.0   # Gaussian fit Y 폭 (pixel)

    # 포화 감지
    saturated:    bool  = False
    sat_ratio:    float = 0.0            # 포화 픽셀 비율 [0.0 – 1.0]

    # 기본 통계 (처리 후 float32 기준)
    frame_mean:   float = 0.0
    frame_std:    float = 0.0
    frame_max:    float = 0.0
    snr:          float = 0.0            # frame_max / frame_std 추정


# ── 메인 클래스 ───────────────────────────────────────────────────────────────

class ImageProcessor:
    """
    카메라 독립 소프트웨어 후처리 파이프라인.

    모든 내부 연산은 float32 — uint16 카메라 다이나믹 레인지 보존.
    process() 한 번 호출로 전체 파이프라인 실행, ProcessedFrame 반환.
    설정 속성은 런타임에 자유롭게 변경 가능.
    """

    def __init__(self):

        # ── 시간 축 연산 ──────────────────────────────────────────────
        self.avg_n:         int         = 5
        self.temporal_mode: TemporalMode = TemporalMode.AVERAGE
        self._buffer:        deque       = deque()
        self._accum:         Optional[np.ndarray] = None
        self._running_sum:   Optional[np.ndarray] = None  # AVERAGE 증분합 (O(H×W))

        # ── Dark frame / Flat field / Background 보정 ─────────────────
        self.dark_frame:     Optional[np.ndarray] = None
        self.dark_enabled:   bool = False
        self.flat_field:     Optional[np.ndarray] = None  # 정규화 (mean=1.0)
        self.flat_enabled:   bool = False
        self.background:     Optional[np.ndarray] = None
        self.bg_sub_enabled: bool = False

        # ── 공간 필터 ─────────────────────────────────────────────────
        self.hot_pixel_enabled:   bool  = False
        self.hot_pixel_threshold: int   = 60     # display 스케일(0-255) 기준 편차
        self.gaussian_enabled:    bool  = False
        self.gaussian_sigma:      float = 1.0
        self.median_enabled:      bool  = False
        self.median_ksize:        int   = 3

        # ── 로그 스케일 ───────────────────────────────────────────────
        self.log_enabled: bool  = False
        self.log_level:   float = 1.0

        # ── 이진화 ────────────────────────────────────────────────────
        self.bin_enabled:   bool = True
        self.bin_threshold: int  = 127
        self.bin_mode:      int  = BIN_BINARY
        self.show_binary:   bool = False

        # ── Display 스트레칭 ──────────────────────────────────────────
        self.display_stretch:       DisplayStretch = DisplayStretch.NORMALIZE
        self.display_min:           float = 0.0
        self.display_max:           float = 65535.0
        self.display_percentile_lo: float = 0.5
        self.display_percentile_hi: float = 99.5

        # ── Centroid ─────────────────────────────────────────────────
        self.centroid_enabled: bool         = True
        self.centroid_mode:    CentroidMode = CentroidMode.BINARY

        # ── 프레임 차분 이미지 ────────────────────────────────────────
        self.temporal_diff_enabled: bool = False
        self._prev_frame: Optional[np.ndarray] = None

        # ── 포화 감지 ─────────────────────────────────────────────────
        self.sat_warn_enabled: bool  = True
        self.sat_threshold:    float = 0.001   # 0.1% 이상 → saturated = True

        # ── FPS 계산 ─────────────────────────────────────────────────
        self._last_time: float = 0.0

    # ── 보정 프레임 캡처 / 해제 ──────────────────────────────────────────────

    def capture_background(self, frame: Optional[np.ndarray] = None) -> None:
        """현재 버퍼 평균 또는 지정 프레임을 배경으로 저장."""
        if frame is not None:
            self.background = frame.astype(np.float32)
        elif self._buffer:
            self.background = np.mean(list(self._buffer), axis=0)

    def clear_background(self) -> None:
        self.background = None
        self.bg_sub_enabled = False

    def capture_dark_frame(self, frame: Optional[np.ndarray] = None) -> None:
        """현재 버퍼 평균 또는 지정 프레임을 Dark frame으로 저장."""
        if frame is not None:
            self.dark_frame = frame.astype(np.float32)
        elif self._buffer:
            self.dark_frame = np.mean(list(self._buffer), axis=0)

    def clear_dark_frame(self) -> None:
        self.dark_frame = None
        self.dark_enabled = False

    def set_flat_field(self, frame: np.ndarray) -> None:
        """Flat field 프레임을 정규화하여 저장 (mean = 1.0 기준)."""
        f = frame.astype(np.float32)
        mean_val = float(f.mean())
        self.flat_field = (f / mean_val) if mean_val > 0 else None

    def clear_flat_field(self) -> None:
        self.flat_field = None
        self.flat_enabled = False

    def reset_accum(self) -> None:
        """ACCUM 모드 누적기 초기화."""
        self._accum = None

    # ── Display 변환 헬퍼 ─────────────────────────────────────────────────────

    def _to_display(self, arr: np.ndarray) -> np.ndarray:
        """float32 배열 → uint8 (현재 display_stretch 모드에 따라 스트레치)."""
        if self.display_stretch == DisplayStretch.MANUAL:
            lo, hi = self.display_min, self.display_max
        elif self.display_stretch == DisplayStretch.PERCENTILE:
            lo = float(np.percentile(arr, self.display_percentile_lo))
            hi = float(np.percentile(arr, self.display_percentile_hi))
        else:  # NORMALIZE
            lo, hi = float(arr.min()), float(arr.max())

        if hi > lo:
            scaled = (arr - lo) / (hi - lo) * 255.0
        else:
            scaled = np.zeros_like(arr)
        return np.clip(scaled, 0, 255).astype(np.uint8)

    # ── 메인 처리 파이프라인 ──────────────────────────────────────────────────

    def process(self, raw: np.ndarray) -> ProcessedFrame:
        """
        raw 프레임을 처리해 ProcessedFrame 반환.

        raw: uint8 / uint16 2D (H×W) 배열.
        내부 연산은 모두 float32 — 원본 다이나믹 레인지 보존.
        """
        import time
        now = time.time()
        fps = 1.0 / max(now - self._last_time, 1e-9) if self._last_time > 0 else 0.0
        self._last_time = now

        # ── 1. float32 변환 ───────────────────────────────────────────
        raw_f = raw.astype(np.float32)
        calc  = raw_f.copy()

        # ── 2. Dark frame 보정 ────────────────────────────────────────
        if self.dark_enabled and self.dark_frame is not None:
            if self.dark_frame.shape == calc.shape:
                calc = np.clip(calc - self.dark_frame, 0.0, None)

        # ── 3. Flat field 보정 ────────────────────────────────────────
        if self.flat_enabled and self.flat_field is not None:
            if self.flat_field.shape == calc.shape:
                flat_safe = np.where(self.flat_field > 0.0, self.flat_field, 1.0)
                calc = calc / flat_safe

        # ── 4. 시간 축 N-frame 연산 ───────────────────────────────────
        if self.temporal_mode == TemporalMode.AVERAGE:
            # Running sum: 새 프레임 더하고 빠진 프레임 빼기 → O(H×W)
            # np.mean(list(buffer), axis=0) 과 수학적으로 동일한 결과
            self._buffer.append(calc)
            if self._running_sum is None or self._running_sum.shape != calc.shape:
                self._running_sum = calc.copy()
            else:
                self._running_sum += calc
            if len(self._buffer) > max(1, self.avg_n):
                self._running_sum -= self._buffer.popleft()
            temporal = self._running_sum / len(self._buffer)

        elif self.temporal_mode == TemporalMode.ACCUM:
            if self._accum is None or self._accum.shape != calc.shape:
                self._accum = np.zeros_like(calc)
            self._accum += calc
            temporal = self._accum.copy()

        else:
            # MAX_PROJ / MIN_PROJ / STD_MAP — buffer 필요
            self._buffer.append(calc)
            if len(self._buffer) > max(1, self.avg_n):
                self._buffer.popleft()
            buf = list(self._buffer)
            if self.temporal_mode == TemporalMode.MAX_PROJ:
                temporal = np.max(buf, axis=0)
            elif self.temporal_mode == TemporalMode.MIN_PROJ:
                temporal = np.min(buf, axis=0)
            elif self.temporal_mode == TemporalMode.STD_MAP:
                temporal = np.std(buf, axis=0) * 8.0
            else:
                temporal = np.mean(buf, axis=0)

        # ── 5. 배경 차분 ─────────────────────────────────────────────
        if self.bg_sub_enabled and self.background is not None:
            if self.background.shape == temporal.shape:
                temporal = np.abs(temporal - self.background)

        # ── 6. 프레임 차분 이미지 ────────────────────────────────────
        if self.temporal_diff_enabled:
            if self._prev_frame is not None and self._prev_frame.shape == temporal.shape:
                temporal = np.abs(temporal - self._prev_frame)
            self._prev_frame = temporal.copy()

        # ── 7. 공간 필터 ─────────────────────────────────────────────
        filtered = temporal.copy()

        if _CV2_OK:
            # ❶ 핫픽셀 제거: display 스케일 편차 기준
            if self.hot_pixel_enabled:
                u8  = self._to_display(filtered)
                med = cv2.medianBlur(u8, 3)
                diff = u8.astype(np.int32) - med.astype(np.int32)
                mask = np.abs(diff) > self.hot_pixel_threshold
                if mask.any():
                    lo_f = float(filtered.min())
                    hi_f = float(filtered.max())
                    if hi_f > lo_f:
                        med_f = lo_f + (med.astype(np.float32) / 255.0) * (hi_f - lo_f)
                    else:
                        med_f = filtered.copy()
                    filtered[mask] = med_f[mask]

            # ❷ Gaussian blur (float32 지원)
            if self.gaussian_enabled:
                ksize = max(3, int(self.gaussian_sigma * 6) | 1)  # 홀수 보장
                filtered = cv2.GaussianBlur(
                    filtered, (ksize, ksize), self.gaussian_sigma)

            # ❸ Median filter (float32, ksize 3 or 5)
            if self.median_enabled:
                ks = self.median_ksize if self.median_ksize % 2 == 1 \
                     else self.median_ksize + 1
                ks = max(3, min(ks, 5))   # cv2 float32: ksize 3 or 5만 지원
                filtered = cv2.medianBlur(filtered, ks)

        # ── 8. 로그 스케일 ────────────────────────────────────────────
        if self.log_enabled:
            filtered = np.log(np.clip(filtered, 0.0, None) + 1.0) * self.log_level

        # ── 9. 이진화 ─────────────────────────────────────────────────
        bin_img_u8: Optional[np.ndarray] = None
        if self.bin_enabled and _CV2_OK:
            u8 = self._to_display(filtered)
            cv2_mode = _BIN_MODE_MAP.get(self.bin_mode, 0)
            _, bin_img_u8 = cv2.threshold(u8, self.bin_threshold, 255, cv2_mode)

        # ── 10. Display 이미지 선택 ───────────────────────────────────
        if self.show_binary and bin_img_u8 is not None:
            display = bin_img_u8
        else:
            display = self._to_display(filtered)

        # ── 11. 포화 감지 (원본 raw 기준) ────────────────────────────
        saturated, sat_ratio = False, 0.0
        if self.sat_warn_enabled and raw.size > 0:
            if np.issubdtype(raw.dtype, np.integer):
                max_val = float(np.iinfo(raw.dtype).max)
            else:
                max_val = float(raw.max())
            sat_pixels = int(np.sum(raw >= max_val))
            sat_ratio  = sat_pixels / raw.size
            saturated  = sat_ratio > self.sat_threshold

        # ── 12. 기본 통계 (처리 후 float32 기준) ─────────────────────
        frame_mean = float(filtered.mean())
        frame_std  = float(filtered.std())
        frame_max  = float(filtered.max())
        snr        = float(frame_max / frame_std) if frame_std > 0 else 0.0

        # ── 13. Centroid ──────────────────────────────────────────────
        cx = cy = None
        brightness = 0
        has_centroid = False
        fit_sigma_x = fit_sigma_y = 0.0
        h, w = raw.shape[:2]

        if self.centroid_enabled:
            if self.centroid_mode == CentroidMode.BINARY and _CV2_OK:
                src = bin_img_u8 if bin_img_u8 is not None \
                      else self._to_display(filtered)
                M = cv2.moments(src)
                if M["m00"] != 0:
                    cx = M["m10"] / M["m00"]
                    cy = M["m01"] / M["m00"]
                    has_centroid = True

            elif self.centroid_mode == CentroidMode.WEIGHTED:
                total = float(filtered.sum())
                if total > 0:
                    ys, xs = np.mgrid[0:h, 0:w]
                    cx = float((xs * filtered).sum() / total)
                    cy = float((ys * filtered).sum() / total)
                    has_centroid = True

            elif self.centroid_mode == CentroidMode.PEAK_MAX:
                idx = int(filtered.argmax())
                cy, cx = divmod(idx, w)
                cx, cy = float(cx), float(cy)
                has_centroid = True

            elif self.centroid_mode == CentroidMode.GAUSSIAN_FIT:
                cx, cy, fit_sigma_x, fit_sigma_y = self._gaussian_fit(filtered, h, w)
                has_centroid = cx is not None
                if not has_centroid:  # 피팅 실패 시 peak max fallback
                    idx = int(filtered.argmax())
                    cy, cx = divmod(idx, w)
                    cx, cy = float(cx), float(cy)
                    has_centroid = True

            if has_centroid:
                iy = min(int(round(cy)), h - 1)
                ix = min(int(round(cx)), w - 1)
                brightness = int(display[iy, ix])

        return ProcessedFrame(
            raw=raw,
            display=display,
            fps=fps,
            centroid_x=cx,
            centroid_y=cy,
            brightness=brightness,
            has_centroid=has_centroid,
            fit_sigma_x=fit_sigma_x,
            fit_sigma_y=fit_sigma_y,
            saturated=saturated,
            sat_ratio=sat_ratio,
            frame_mean=frame_mean,
            frame_std=frame_std,
            frame_max=frame_max,
            snr=snr,
        )

    @staticmethod
    def _gaussian_fit(img: np.ndarray, h: int, w: int):
        """2D Gaussian 피팅. 반환: (cx, cy, sigma_x, sigma_y) 또는 실패 시 (None,)*4."""
        try:
            from scipy.optimize import curve_fit

            def _gauss2d(xy, amp, x0, y0, sx, sy, bg):
                x, y = xy
                return bg + amp * np.exp(
                    -((x - x0)**2 / (2*sx**2) + (y - y0)**2 / (2*sy**2))
                )

            # 초기 추정: 최대값 위치 기준 crop (빠름 + 안정)
            iy_pk, ix_pk = np.unravel_index(int(img.argmax()), img.shape)
            half = 30
            x0c = max(0, ix_pk - half); x1c = min(w, ix_pk + half)
            y0c = max(0, iy_pk - half); y1c = min(h, iy_pk + half)
            crop = img[y0c:y1c, x0c:x1c].astype(np.float64)

            yc_g, xc_g = np.mgrid[y0c:y1c, x0c:x1c]
            xy = (xc_g.ravel(), yc_g.ravel())
            z  = crop.ravel()

            amp0 = float(crop.max() - crop.min())
            bg0  = float(crop.min())
            p0   = [amp0, float(ix_pk), float(iy_pk), 5.0, 5.0, bg0]
            lo   = [0,    float(x0c),   float(y0c),   0.3, 0.3, -float('inf')]
            hi   = [float('inf'), float(x1c), float(y1c), half*2, half*2, float('inf')]

            popt, _ = curve_fit(_gauss2d, xy, z, p0=p0,
                                bounds=(lo, hi), maxfev=2000)
            return popt[1], popt[2], abs(popt[3]), abs(popt[4])
        except Exception:
            return None, None, 0.0, 0.0

    def reset_buffer(self) -> None:
        """버퍼, 누적기, EMA, 이전 프레임 모두 초기화."""
        self._buffer.clear()
        self._last_time   = 0.0
        self._accum       = None
        self._running_sum = None
        self._prev_frame  = None
