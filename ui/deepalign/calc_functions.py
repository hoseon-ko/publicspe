import numpy as np
from scipy.ndimage import laplace

# ── 밝기 (Brightness) 8종 ─────────────────────────────────────────────
# 모든 함수는 2D ndarray 입력. NaN 처리는 각 함수 내부에서 수행.

def calc_function_1(arr: np.ndarray) -> float:
    """밝기 1: 평균 (Mean)"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    return float(np.mean(flat)) if flat.size > 0 else 0.0

def calc_function_2(arr: np.ndarray) -> float:
    """밝기 2: 중앙값 (Median)"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    return float(np.median(flat)) if flat.size > 0 else 0.0

def calc_function_3(arr: np.ndarray) -> float:
    """밝기 3: RMS 밝기 = √(Σx²/N)"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    return float(np.sqrt(np.mean(flat ** 2))) if flat.size > 0 else 0.0

def calc_function_4(arr: np.ndarray) -> float:
    """밝기 4: 상위 5% 평균 (P95 이상 픽셀 평균)"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    thresh = np.percentile(flat, 95)
    top = flat[flat >= thresh]
    return float(np.mean(top)) if top.size > 0 else 0.0

def calc_function_5(arr: np.ndarray) -> float:
    """밝기 5: 상위 1% 평균 (P99 이상 픽셀 평균)"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    thresh = np.percentile(flat, 99)
    top = flat[flat >= thresh]
    return float(np.mean(top)) if top.size > 0 else 0.0

def calc_function_6(arr: np.ndarray) -> float:
    """밝기 6: P90 밝기 (90th percentile)"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    return float(np.percentile(flat, 90)) if flat.size > 0 else 0.0

def calc_function_7(arr: np.ndarray) -> float:
    """밝기 7: 밝기 인덱스 BI = mean / max  [0~1]"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    mx = float(np.max(flat))
    return float(np.mean(flat)) / mx if mx > 0 else 0.0

def calc_function_8(arr: np.ndarray) -> float:
    """밝기 8: Log 평균 밝기 = mean(log10(양수픽셀))"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    pos = flat[flat > 0]
    return float(np.mean(np.log10(pos))) if pos.size > 0 else 0.0


# ── 대비 (Contrast) 9종 ──────────────────────────────────────────────
# Weber/SNR/Dynamic Range: 배경 미지정 시 ROI 하위 20% 자동 추정

def calc_function_9(arr: np.ndarray) -> float:
    """대비 1: Michelson 전역 = (max−min)/(max+min)"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    mn, mx = float(np.min(flat)), float(np.max(flat))
    denom = mx + mn
    return (mx - mn) / denom if denom > 0 else 0.0

def calc_function_10(arr: np.ndarray) -> float:
    """대비 2: Michelson 로컬 (16×16 패치 단위 평균)"""
    a = np.where(np.isfinite(arr), arr, 0.0).astype(np.float64)
    if a.size == 0:
        return 0.0
    PATCH = 16
    h, w = a.shape[:2]
    patches = []
    for y in range(0, h - PATCH + 1, PATCH):
        for x in range(0, w - PATCH + 1, PATCH):
            blk = a[y:y + PATCH, x:x + PATCH].ravel()
            bmin, bmax = blk.min(), blk.max()
            denom = bmax + bmin
            if denom > 0:
                patches.append((bmax - bmin) / denom)
    if patches:
        return float(np.mean(patches))
    # 패치 없으면 전역 Michelson 반환
    mn, mx = a.min(), a.max()
    denom = mx + mn
    return float((mx - mn) / denom) if denom > 0 else 0.0

def calc_function_11(arr: np.ndarray) -> float:
    """대비 3: RMS 대비 = σ/μ"""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    mean = float(np.mean(flat))
    return float(np.std(flat)) / mean if mean > 0 else 0.0

def calc_function_12(arr: np.ndarray, bg_arr: np.ndarray | None = None) -> float:
    """대비 4: Weber 대비 = (신호평균 − 배경평균) / 배경평균
    bg_arr 제공 시 해당 픽셀을 배경으로, 없으면 하위 20% 자동 추정."""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    if bg_arr is not None and bg_arr.size > 0:
        bg = bg_arr.ravel()
        bg = bg[np.isfinite(bg)]
    else:
        bg_thresh = np.percentile(flat, 20)
        bg = flat[flat <= bg_thresh]
    bg_mean = float(np.mean(bg)) if bg.size > 0 else float(np.min(flat))
    if bg_mean <= 0:
        return 0.0
    return (float(np.mean(flat)) - bg_mean) / bg_mean

def calc_function_13(arr: np.ndarray, bg_arr: np.ndarray | None = None) -> float:
    """대비 5: SNR = 평균신호 / 배경노이즈σ
    bg_arr 제공 시 해당 픽셀을 배경으로, 없으면 하위 20% 자동 추정."""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    if bg_arr is not None and bg_arr.size > 0:
        bg = bg_arr.ravel()
        bg = bg[np.isfinite(bg)]
    else:
        bg_thresh = np.percentile(flat, 20)
        bg = flat[flat <= bg_thresh]
    noise = float(np.std(bg)) if bg.size > 0 else 1e-6
    if noise <= 0:
        noise = 1e-6
    return float(np.mean(flat)) / noise

def calc_function_14(arr: np.ndarray, bg_arr: np.ndarray | None = None) -> float:
    """대비 6: Dynamic Range [dB] = 20·log10(max / noise_floor)
    bg_arr 제공 시 해당 픽셀을 배경으로, 없으면 하위 20% 자동 추정."""
    flat = arr.ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 0.0
    mx = float(np.max(flat))
    if mx <= 0:
        return 0.0
    if bg_arr is not None and bg_arr.size > 0:
        bg = bg_arr.ravel()
        bg = bg[np.isfinite(bg)]
    else:
        bg_thresh = np.percentile(flat, 20)
        bg = flat[flat <= bg_thresh]
    noise = float(np.std(bg)) if bg.size > 0 else 1e-6
    if noise <= 0:
        noise = 1e-6
    return 20.0 * float(np.log10(mx / noise))

def calc_function_15(arr: np.ndarray) -> float:
    """대비 7: 선명도 Laplacian σ² = var(laplace(ROI))"""
    a = np.where(np.isfinite(arr), arr, 0.0).astype(np.float64)
    if a.size == 0:
        return 0.0
    return float(np.var(laplace(a)))

def calc_function_16(arr: np.ndarray) -> float:
    """대비 8: 프로파일 Michelson (H) — 수평 행평균 프로파일"""
    a = np.where(np.isfinite(arr), arr, 0.0).astype(np.float64)
    if a.ndim < 2 or a.shape[1] == 0:
        return 0.0
    prof = a.mean(axis=0)
    mn, mx = prof.min(), prof.max()
    denom = mx + mn
    return float((mx - mn) / denom) if denom > 0 else 0.0

def calc_function_17(arr: np.ndarray) -> float:
    """대비 9: 프로파일 Michelson (V) — 수직 열평균 프로파일"""
    a = np.where(np.isfinite(arr), arr, 0.0).astype(np.float64)
    if a.ndim < 2 or a.shape[0] == 0:
        return 0.0
    prof = a.mean(axis=1)
    mn, mx = prof.min(), prof.max()
    denom = mx + mn
    return float((mx - mn) / denom) if denom > 0 else 0.0
