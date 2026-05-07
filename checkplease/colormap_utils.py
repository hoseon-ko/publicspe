"""
ui/colormap_utils.py
numpy 배열 컬러맵 변환 유틸리티.
"""
import numpy as np
from PyQt6.QtGui import QPixmap, QImage
from typing import Tuple, Union

def apply_colormap(image: np.ndarray, 
                   cmap: str = 'jet',
                   vmin: Union[float, None] = None,
                   vmax: Union[float, None] = None) -> np.ndarray:
    """2D float 이미지 → RGBA uint8.

    vmin/vmax 가 명시된 경우:
      - 픽셀 < vmin  → 파랑 (0, 0, 255)
      - 픽셀 > vmax  → 빨강 (255, 0, 0)
      - 그 사이      → [vmin, vmax] 구간을 [0,1]로 리스케일 후 컬러맵 적용
    vmin/vmax 가 None 이면 이미지 전체 범위로 자동 설정 (under/over 없음).
    """
    raw = image.astype(np.float64)
    auto_lo = vmin is None
    auto_hi = vmax is None
    lo = float(raw.min()) if auto_lo else float(vmin)
    hi = float(raw.max()) if auto_hi else float(vmax)

    # under / over 마스크 (자동 범위면 없음)
    under = np.zeros(raw.shape, dtype=bool) if auto_lo else (raw < lo)
    over  = np.zeros(raw.shape, dtype=bool) if auto_hi else (raw > hi)

    if hi > lo:
        f = np.clip((raw - lo) / (hi - lo), 0.0, 1.0)
    else:
        f = np.zeros_like(raw)

    if cmap == 'jet':
        r = np.clip(1.5 - np.abs(4.0 * f - 3.0), 0, 1)
        g = np.clip(1.5 - np.abs(4.0 * f - 2.0), 0, 1)
        b = np.clip(1.5 - np.abs(4.0 * f - 1.0), 0, 1)
    elif cmap == 'grey':
        r = g = b = f
    elif cmap == 'hot':
        r = np.clip(f * 3.0, 0, 1)
        g = np.clip(f * 3.0 - 1.0, 0, 1)
        b = np.clip(f * 3.0 - 2.0, 0, 1)
    elif cmap == 'viridis':
        r = np.clip(0.267 + 0.005 * f + 2.33 * f**2 - 1.98 * f**3, 0, 1)
        g = np.clip(0.005 + 1.40 * f - 0.55 * f**2, 0, 1)
        b = np.clip(0.329 + 1.50 * f - 1.85 * f**2, 0, 1)
    elif cmap == 'plasma':
        r = np.clip(0.05 + 2.0 * f - 0.7 * f**2, 0, 1)
        g = np.clip(0.03 * f + 1.2 * f**2 - 0.5 * f**3, 0, 1)
        b = np.clip(0.55 - 0.8 * f + 0.5 * f**2, 0, 1)
    else:
        r = g = b = f

    # under → 파랑, over → 빨강
    r[under] = 0.0;  g[under] = 0.0;  b[under] = 1.0
    r[over]  = 1.0;  g[over]  = 0.0;  b[over]  = 0.0

    rgba = np.stack([r, g, b, np.ones_like(f)], axis=-1)
    return (rgba * 255).astype(np.uint8)


def ndarray_to_qpixmap(rgba: np.ndarray) -> QPixmap:
    h, w = rgba.shape[:2]
    rgba_c = np.ascontiguousarray(rgba)
    img = QImage(rgba_c.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())
