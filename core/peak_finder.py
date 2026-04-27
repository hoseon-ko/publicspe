"""
core/peak_finder.py
스펙트럼 1D 프로파일에서 피크를 검출하고 Gaussian / Lorentzian 피팅을 수행.

의존성:
  numpy  — 필수
  scipy  — 선택적 (없으면 단순 로컬 최대값 폴백 + 피팅 비활성)

사용 예:
    finder = PeakFinder()
    peaks  = finder.find_peaks(profile_array)
    peaks  = [finder.fit_gaussian(profile_array, p) for p in peaks]
    # 또는 한 번에:
    peaks  = finder.find_and_fit(profile_array, fit_type="gaussian")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    from scipy.signal import find_peaks as _sp_find_peaks, peak_widths as _sp_peak_widths
    from scipy.optimize import curve_fit as _sp_curve_fit
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False


# ── 결과 데이터 클래스 ────────────────────────────────────────────────────────

@dataclass
class PeakResult:
    """단일 피크 정보."""
    position:   float           # 검출된 피크 위치 (pixel index)
    height:     float           # 피크 강도
    fwhm:       float           # 반높이 너비 (pixels)
    area:       float           # 피크 아래 면적 (사다리꼴 적분 추정)

    # 피팅 결과 (피팅 수행 후 채워짐)
    fit_center: Optional[float] = None   # 피팅된 중심
    fit_sigma:  Optional[float] = None   # Gaussian σ 또는 Lorentzian γ
    fit_fwhm:   Optional[float] = None   # 피팅된 FWHM
    fit_r2:     Optional[float] = None   # 결정계수 R²
    fit_type:   str             = "none" # "gaussian" / "lorentzian" / "none"

    @property
    def best_center(self) -> float:
        """피팅 결과가 있으면 fit_center, 없으면 position."""
        return self.fit_center if self.fit_center is not None else self.position

    @property
    def best_fwhm(self) -> float:
        """피팅 결과가 있으면 fit_fwhm, 없으면 fwhm."""
        return self.fit_fwhm if self.fit_fwhm is not None else self.fwhm


# ── 메인 클래스 ───────────────────────────────────────────────────────────────

class PeakFinder:
    """
    1D 스펙트럼 프로파일 피크 검출 + 피팅.

    모든 설정은 속성으로 노출 — 런타임에 자유롭게 변경 가능.
    """

    def __init__(self):
        # 검출 파라미터
        self.min_height:  Optional[float] = None   # None → 자동 (최소값 + range*5%)
        self.prominence:  float = 0.05             # 상대 prominence (0 ~ 1)
        self.min_width:   int   = 2                # 최소 피크 너비 (pixels)
        self.max_peaks:   int   = 20               # 최대 검출 피크 수

        # 피팅 파라미터
        self.fit_window:  int   = 20               # 피팅 윈도우 ± pixels

    # ── 피크 검출 ─────────────────────────────────────────────────────────────

    def find_peaks(self, profile: np.ndarray) -> list[PeakResult]:
        """
        1D 프로파일에서 피크 검출.

        Returns:
            PeakResult 목록 (높이 내림차순 정렬). 검출 실패 시 빈 리스트.
        """
        y = np.asarray(profile, dtype=np.float64).ravel()
        if y.size < 3:
            return []

        y_min   = float(y.min())
        y_range = float(y.max()) - y_min
        if y_range == 0:
            return []

        height_thresh = self.min_height if self.min_height is not None \
                        else y_min + y_range * 0.05
        prom_thresh   = y_range * self.prominence

        if _SCIPY_OK:
            indices, _ = _sp_find_peaks(
                y,
                height=height_thresh,
                prominence=prom_thresh,
                width=self.min_width,
            )
            if len(indices) == 0:
                return []

            # 높이 내림차순 정렬, max_peaks 제한
            order   = np.argsort(-y[indices])[:self.max_peaks]
            indices = indices[order]

            widths_px, _, _, _ = _sp_peak_widths(y, indices, rel_height=0.5)

            results = []
            for i, idx in enumerate(indices):
                half_w = max(int(widths_px[i] * 2), 1)
                lo = max(0, idx - half_w)
                hi = min(len(y) - 1, idx + half_w)
                area = float(np.trapz(y[lo:hi + 1]))
                results.append(PeakResult(
                    position=float(idx),
                    height=float(y[idx]),
                    fwhm=float(widths_px[i]),
                    area=area,
                ))
            return results

        else:
            # scipy 없음 — 단순 로컬 최대값 폴백
            results = []
            for i in range(1, len(y) - 1):
                if y[i] > y[i - 1] and y[i] > y[i + 1] and y[i] >= height_thresh:
                    results.append(PeakResult(
                        position=float(i),
                        height=float(y[i]),
                        fwhm=float(self.min_width),
                        area=float(y[i]),
                    ))
            results.sort(key=lambda p: -p.height)
            return results[:self.max_peaks]

    # ── Gaussian 피팅 ─────────────────────────────────────────────────────────

    @staticmethod
    def _gaussian(x: np.ndarray, amp: float, center: float,
                  sigma: float, offset: float) -> np.ndarray:
        return amp * np.exp(-((x - center) ** 2) / (2 * sigma ** 2)) + offset

    def fit_gaussian(self, profile: np.ndarray, peak: PeakResult) -> PeakResult:
        """
        지정 피크 주변 윈도우에 Gaussian 피팅.
        실패 시 원본 PeakResult 그대로 반환.
        """
        if not _SCIPY_OK:
            return peak

        y = np.asarray(profile, dtype=np.float64).ravel()
        ci = int(round(peak.position))
        lo = max(0, ci - self.fit_window)
        hi = min(len(y), ci + self.fit_window + 1)
        x_win = np.arange(lo, hi, dtype=np.float64)
        y_win = y[lo:hi]

        try:
            p0 = [
                peak.height - float(y_win.min()),
                peak.position,
                max(peak.fwhm / 2.355, 0.5),
                float(y_win.min()),
            ]
            popt, _ = _sp_curve_fit(
                PeakFinder._gaussian, x_win, y_win, p0=p0, maxfev=2000)
            amp, center, sigma, offset = popt
            y_pred = PeakFinder._gaussian(x_win, *popt)
            ss_res = float(np.sum((y_win - y_pred) ** 2))
            ss_tot = float(np.sum((y_win - y_win.mean()) ** 2))

            peak.fit_center = float(center)
            peak.fit_sigma  = float(abs(sigma))
            peak.fit_fwhm   = float(abs(sigma) * 2.3548)
            peak.fit_r2     = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
            peak.fit_type   = "gaussian"
        except Exception:
            pass

        return peak

    # ── Lorentzian 피팅 ───────────────────────────────────────────────────────

    @staticmethod
    def _lorentzian(x: np.ndarray, amp: float, center: float,
                    gamma: float, offset: float) -> np.ndarray:
        return amp * (gamma ** 2 / ((x - center) ** 2 + gamma ** 2)) + offset

    def fit_lorentzian(self, profile: np.ndarray, peak: PeakResult) -> PeakResult:
        """
        지정 피크 주변 윈도우에 Lorentzian 피팅.
        실패 시 원본 PeakResult 그대로 반환.
        """
        if not _SCIPY_OK:
            return peak

        y = np.asarray(profile, dtype=np.float64).ravel()
        ci = int(round(peak.position))
        lo = max(0, ci - self.fit_window)
        hi = min(len(y), ci + self.fit_window + 1)
        x_win = np.arange(lo, hi, dtype=np.float64)
        y_win = y[lo:hi]

        try:
            p0 = [
                peak.height - float(y_win.min()),
                peak.position,
                max(peak.fwhm / 2.0, 0.5),
                float(y_win.min()),
            ]
            popt, _ = _sp_curve_fit(
                PeakFinder._lorentzian, x_win, y_win, p0=p0, maxfev=2000)
            amp, center, gamma, offset = popt
            y_pred = PeakFinder._lorentzian(x_win, *popt)
            ss_res = float(np.sum((y_win - y_pred) ** 2))
            ss_tot = float(np.sum((y_win - y_win.mean()) ** 2))

            peak.fit_center = float(center)
            peak.fit_sigma  = float(abs(gamma))
            peak.fit_fwhm   = float(abs(gamma) * 2.0)
            peak.fit_r2     = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
            peak.fit_type   = "lorentzian"
        except Exception:
            pass

        return peak

    # ── 편의 메서드 ───────────────────────────────────────────────────────────

    def find_and_fit(self, profile: np.ndarray,
                     fit_type: str = "gaussian") -> list[PeakResult]:
        """
        find_peaks + 각 피크에 피팅 적용.

        Args:
            profile:  1D numpy 배열
            fit_type: "gaussian" / "lorentzian" / "none"

        Returns:
            피팅 결과가 포함된 PeakResult 목록
        """
        peaks = self.find_peaks(profile)
        if fit_type == "none":
            return peaks
        for i, p in enumerate(peaks):
            if fit_type == "gaussian":
                peaks[i] = self.fit_gaussian(profile, p)
            elif fit_type == "lorentzian":
                peaks[i] = self.fit_lorentzian(profile, p)
        return peaks
