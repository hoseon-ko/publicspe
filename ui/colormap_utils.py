"""
ui/colormap_utils.py
numpy 배열 컬러맵 변환 유틸리티.
"""
import numpy as np
from PyQt6.QtGui import QPixmap, QImage


def apply_colormap(image: np.ndarray, cmap: str = 'jet',
                   vmin: float | None = None,
                   vmax: float | None = None) -> np.ndarray:
    """2D float 이미지 → RGBA uint8.
    vmin/vmax 지정 시 해당 범위를 [0,1]로 클리핑·정규화한다."""
    f = image.astype(np.float64)
    lo = float(f.min()) if vmin is None else float(vmin)
    hi = float(f.max()) if vmax is None else float(vmax)
    if hi > lo:
        f = np.clip((f - lo) / (hi - lo), 0.0, 1.0)
    else:
        f = np.zeros_like(f)

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

    rgba = np.stack([r, g, b, np.ones_like(f)], axis=-1)
    return (rgba * 255).astype(np.uint8)


def ndarray_to_qpixmap(rgba: np.ndarray) -> QPixmap:
    h, w = rgba.shape[:2]
    rgba_c = np.ascontiguousarray(rgba)
    img = QImage(rgba_c.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())
