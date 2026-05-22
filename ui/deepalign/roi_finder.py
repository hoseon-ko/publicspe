"""
roi_finder.py — 드래그 ROI 기반 패턴 자동검출 + 배경 링 ROI 계산
=================================================================
1. find_pattern_roi()  : coarse box → fine signal ROI (강도 기반 자동검출)
2. compute_ring_bg_roi(): signal ROI 외곽 ring → BG ROI bbox 반환

알고리즘 (find_pattern_roi):
  1. coarse ROI 크롭
  2. Gaussian 블러 (blur_sigma)
  3. 강도 임계값 (percentile) 로 이진 마스크
  4. 연결 성분 중 가장 큰 blob 선택
  5. Margin 확장 후 원본 좌표계 변환

알고리즘 (compute_ring_bg_roi):
  signal ROI 기준으로 gap + thickness 픽셀만큼 확장한 외곽 bbox 반환.
  실제 이미지 경계를 초과하지 않도록 클리핑.
"""
from __future__ import annotations

import numpy as np
from typing import Optional, Tuple


def find_pattern_roi(
    img: np.ndarray,
    coarse_roi: Tuple[int, int, int, int],  # (x, y, w, h)
    *,
    blur_sigma: float = 2.0,
    threshold_pct: float = 70.0,
    margin: int = 5,
    search_expand: int = 0,
) -> Optional[Tuple[int, int, int, int]]:
    """
    coarse_roi 영역(+ search_expand 확장) 내에서 가장 밝은 패턴을 자동 검출.

    Parameters
    ----------
    blur_sigma     : Gaussian 블러 반경 (0 이면 생략)
    threshold_pct  : crop 내 이 백분위수 이상을 "신호" 픽셀로 분류
                     낮을수록 더 넓게 검출 (50 = 상위 50%)
    margin         : 검출된 blob 경계 바깥 추가 여백 (픽셀)
    search_expand  : coarse_roi 바깥을 이 픽셀만큼 추가 탐색.
                     0이면 coarse_roi 안만 본다.
                     > 0이면 반복 호출 시 ROI가 영원히 수축하지 않음.

    반환: (x, y, w, h) 원본 좌표계. 실패 시 None.
    """
    from scipy.ndimage import gaussian_filter, label as ndlabel

    if img is None or coarse_roi is None:
        return None

    H, W = img.shape[:2]
    cx, cy, cw, ch = [int(v) for v in coarse_roi]

    # search_expand 만큼 검색 영역 확장 (이미지 경계 클리핑)
    sx = max(0, cx - search_expand)
    sy = max(0, cy - search_expand)
    sx1 = min(W, cx + cw + search_expand)
    sy1 = min(H, cy + ch + search_expand)
    sw, sh = sx1 - sx, sy1 - sy

    if sw < 1 or sh < 1:
        return None

    crop = img[sy:sy1, sx:sx1].astype(np.float32)
    if crop.size == 0:
        return None

    smoothed = gaussian_filter(crop, sigma=max(0.01, blur_sigma)) if blur_sigma > 0 else crop.copy()

    thr = np.percentile(smoothed, max(0.0, min(threshold_pct, 99.9)))
    binary = (smoothed >= thr).astype(np.uint8)

    try:
        labeled, num_feats = ndlabel(binary)
    except Exception:
        return None

    if num_feats == 0:
        return None

    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0
    largest = int(np.argmax(sizes))

    ys, xs = np.where(labeled == largest)
    if len(xs) == 0:
        return None

    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())

    # margin 확장 (검색 crop 내에서 클리핑)
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(sw - 1, x1 + margin)
    y1 = min(sh - 1, y1 + margin)

    # 원본 이미지 좌표계로 변환
    fx, fy = sx + x0, sy + y0
    fw, fh = x1 - x0 + 1, y1 - y0 + 1

    if fw < 4 or fh < 4:
        return None

    return (fx, fy, fw, fh)


def compute_ring_bg_roi(
    sig_roi: Tuple[int, int, int, int],
    img_shape: Tuple[int, int],          # (H, W)
    *,
    gap: int = 2,                         # 신호 ROI와의 간격 (픽셀)
    thickness: int = 10,                  # 링 두께 (픽셀)
) -> Optional[Tuple[int, int, int, int]]:
    """
    신호 ROI 외곽 ring의 bounding box를 반환.

    반환값은 신호 ROI 주위를 둘러싸는 바깥 박스 전체이며,
    이 박스에서 내부(sig_roi + gap) 영역을 제외한 부분이 실질적 링입니다.

    반환: (x, y, w, h) 원본 좌표계. 실패 시 None.
    """
    if sig_roi is None:
        return None

    H, W = img_shape[:2]
    sx, sy, sw, sh = [int(v) for v in sig_roi]

    # 내부 제외 경계 (gap 포함)
    inner_x0 = sx - gap
    inner_y0 = sy - gap
    inner_x1 = sx + sw + gap
    inner_y1 = sy + sh + gap

    # 링 외곽 경계
    outer_x0 = max(0, inner_x0 - thickness)
    outer_y0 = max(0, inner_y0 - thickness)
    outer_x1 = min(W, inner_x1 + thickness)
    outer_y1 = min(H, inner_y1 + thickness)

    ow = outer_x1 - outer_x0
    oh = outer_y1 - outer_y0

    if ow < 4 or oh < 4:
        return None

    return (outer_x0, outer_y0, ow, oh)


def extract_ring_pixels(
    img: np.ndarray,
    sig_roi: Tuple[int, int, int, int],
    *,
    gap: int = 2,
    thickness: int = 10,
) -> Optional[np.ndarray]:
    """
    실제 링 마스크를 적용하여 배경 픽셀만 1D array로 추출.
    """
    outer = compute_ring_bg_roi(sig_roi, img.shape, gap=gap, thickness=thickness)
    if outer is None:
        return None

    H, W = img.shape[:2]
    sx, sy, sw, sh = [int(v) for v in sig_roi]
    ox, oy, ow, oh = outer

    mask = np.zeros((H, W), dtype=bool)
    mask[oy:oy+oh, ox:ox+ow] = True
    # 내부 (gap 포함) 제외
    inner_x0 = max(0, sx - gap)
    inner_y0 = max(0, sy - gap)
    inner_x1 = min(W, sx + sw + gap)
    inner_y1 = min(H, sy + sh + gap)
    mask[inner_y0:inner_y1, inner_x0:inner_x1] = False

    pixels = img[mask]
    return pixels if len(pixels) > 0 else None
