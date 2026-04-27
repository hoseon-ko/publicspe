"""
core/wavelength_cal.py
스펙트럼 파장 캘리브레이션: 픽셀 좌표 ↔ 파장(nm) 다항식 변환.

방식:
  알려진 피크 (pixel, nm) 쌍을 입력 → np.polyfit 다항식 피팅.
  저장/불러오기: JSON.

사용 예:
    cal = WavelengthCalibration()
    cal.calibrate([100, 512, 900], [404.7, 546.1, 696.5])
    print(cal.summary())               # "2차 | RMS=0.0023nm | ..."
    nm_axis = cal.px_to_nm(np.arange(1024))
    px = cal.nm_to_px(546.1)          # → ~512
    cal.save("cal_2026.json")
    cal.load("cal_2026.json")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np


class WavelengthCalibration:
    """
    픽셀 → 파장(nm) 다항식 캘리브레이션.

    속성:
        is_calibrated  캘리브레이션 완료 여부
        n_points       등록된 포인트 수
        residual_rms   피팅 잔차 RMS (nm)
    """

    def __init__(self):
        self._coeff:        Optional[np.ndarray] = None   # np.polyfit 계수
        self._degree:       int   = 2
        self._px_pts:       list[float] = []
        self._nm_pts:       list[float] = []
        self._residual_rms: float = 0.0
        self.is_calibrated: bool  = False

    # ── 캘리브레이션 ──────────────────────────────────────────────────────────

    def calibrate(self,
                  pixel_pts:      list[float],
                  wavelength_pts: list[float],
                  degree:         int = 2) -> np.ndarray:
        """
        알려진 (pixel, nm) 쌍으로 다항식 피팅.

        Args:
            pixel_pts:      픽셀 좌표 목록 (최소 degree+1 개)
            wavelength_pts: 대응하는 파장(nm) 목록
            degree:         다항식 차수 (1=선형, 2=2차 권장)

        Returns:
            np.polyfit 계수 배열 (고차항부터)

        Raises:
            ValueError: 포인트 수 부족
        """
        n_needed = degree + 1
        if len(pixel_pts) < n_needed:
            raise ValueError(
                f"캘리브레이션 포인트 {len(pixel_pts)}개 — "
                f"{degree}차 다항식에는 최소 {n_needed}개 필요"
            )
        px = np.array(pixel_pts,      dtype=np.float64)
        nm = np.array(wavelength_pts, dtype=np.float64)

        self._coeff  = np.polyfit(px, nm, degree)
        self._degree = degree
        self._px_pts = list(pixel_pts)
        self._nm_pts = list(wavelength_pts)

        fitted             = np.polyval(self._coeff, px)
        self._residual_rms = float(np.sqrt(np.mean((fitted - nm) ** 2)))
        self.is_calibrated = True
        return self._coeff

    def add_point(self, pixel: float, wavelength_nm: float,
                  degree: int = 2) -> bool:
        """
        포인트 추가 후 자동 재피팅.

        Returns:
            True — 포인트 수 충분해 피팅 완료.
            False — 아직 포인트 부족.
        """
        self._px_pts.append(float(pixel))
        self._nm_pts.append(float(wavelength_nm))
        if len(self._px_pts) >= degree + 1:
            self.calibrate(self._px_pts, self._nm_pts, degree)
            return True
        return False

    def remove_point(self, index: int, degree: int = 2) -> bool:
        """인덱스로 포인트 제거 후 재피팅 시도."""
        if not (0 <= index < len(self._px_pts)):
            return False
        self._px_pts.pop(index)
        self._nm_pts.pop(index)
        if len(self._px_pts) >= degree + 1:
            self.calibrate(self._px_pts, self._nm_pts, degree)
            return True
        self._coeff        = None
        self.is_calibrated = False
        return False

    def clear(self) -> None:
        """모든 포인트 및 캘리브레이션 초기화."""
        self._px_pts       = []
        self._nm_pts       = []
        self._coeff        = None
        self._residual_rms = 0.0
        self.is_calibrated = False

    # ── 변환 ─────────────────────────────────────────────────────────────────

    def px_to_nm(self, px: np.ndarray) -> np.ndarray:
        """
        픽셀 배열 → 파장(nm) 배열.
        미캘리브레이션 시 px를 float64로 그대로 반환.
        """
        arr = np.asarray(px, dtype=np.float64)
        if not self.is_calibrated or self._coeff is None:
            return arr
        return np.polyval(self._coeff, arr)

    def nm_to_px(self, nm: float) -> float:
        """
        단일 파장(nm) → 픽셀 위치 (수치 역변환).
        다항식 roots에서 실수 근 중 가장 가까운 값 반환.
        """
        if not self.is_calibrated or self._coeff is None:
            return nm
        shifted       = self._coeff.copy()
        shifted[-1]  -= nm
        roots         = np.roots(shifted)
        real_roots    = roots[np.isreal(roots)].real
        if len(real_roots) == 0:
            return nm
        # nm 값과 가장 가까운 근 선택 (대략적 초기값으로 사용)
        return float(real_roots[np.argmin(np.abs(real_roots - nm))])

    # ── 저장 / 불러오기 ───────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """JSON으로 캘리브레이션 저장."""
        data = {
            "degree":       self._degree,
            "coefficients": self._coeff.tolist() if self._coeff is not None else [],
            "pixel_pts":    self._px_pts,
            "nm_pts":       self._nm_pts,
            "residual_rms": self._residual_rms,
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: str) -> None:
        """JSON 캘리브레이션 불러오기."""
        data           = json.loads(Path(path).read_text(encoding="utf-8"))
        self._degree   = data.get("degree", 2)
        self._px_pts   = data.get("pixel_pts", [])
        self._nm_pts   = data.get("nm_pts", [])
        self._residual_rms = data.get("residual_rms", 0.0)
        coeff          = data.get("coefficients", [])
        if coeff:
            self._coeff        = np.array(coeff, dtype=np.float64)
            self.is_calibrated = True
        else:
            self._coeff        = None
            self.is_calibrated = False

    # ── 정보 ─────────────────────────────────────────────────────────────────

    @property
    def n_points(self) -> int:
        return len(self._px_pts)

    @property
    def residual_rms(self) -> float:
        return self._residual_rms

    @property
    def points(self) -> list[tuple[float, float]]:
        """등록된 (pixel, nm) 쌍 목록."""
        return list(zip(self._px_pts, self._nm_pts))

    def summary(self) -> str:
        if not self.is_calibrated:
            return (f"미캘리브레이션 "
                    f"({self.n_points}개 / 최소 {self._degree + 1}개 필요)")
        coeff_str = ", ".join(f"{c:.5g}" for c in self._coeff)
        return (
            f"{self._degree}차 다항식 | {self.n_points}개 포인트 | "
            f"RMS={self._residual_rms:.4f} nm | 계수=[{coeff_str}]"
        )

    def __repr__(self) -> str:
        return f"WavelengthCalibration({self.summary()})"
