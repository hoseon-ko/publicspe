"""스캔 결과 분석 헬퍼 — centroid / sharpness / 썸네일.

scan worker 의 process_fn 으로 주입되어 각 포인트의 frame 을 정량 분석한 dict 를
반환. main_tab._on_point_done 이 이 dict 를 받아 da_table / af_table /
da_plot_panel / af_plot_panel 을 채운다.

Qt 의존 없음 (numpy 만). Qt 변환은 별도 헬퍼.
"""

from __future__ import annotations

from typing import Optional
import numpy as np


def _to_gray2d(frame) -> np.ndarray:
    f = np.asarray(frame)
    if f.ndim == 3:
        f = f.mean(axis=2)
    return f.astype(np.float64, copy=False)


def compute_centroid_stats(frame) -> dict:
    """intensity-weighted centroid + 2차 모먼트 sigma + 간단 SNR.

    Returns:
        {
          "cent_x": float (px), "cent_y": float (px),
          "sigma_x": float (px), "sigma_y": float (px),
          "snr":     float (peak / global std),
          "peak":    float, "background": float,
        }
    """
    f = _to_gray2d(frame)
    bg = float(np.median(f))
    f0 = np.clip(f - bg, 0.0, None)
    total = f0.sum()
    H, W = f0.shape

    if total <= 0:
        return {
            "cent_x": 0.0, "cent_y": 0.0,
            "sigma_x": 0.0, "sigma_y": 0.0,
            "snr": 0.0, "peak": 0.0, "background": bg,
        }

    yy, xx = np.mgrid[0:H, 0:W]
    cx = float((xx * f0).sum() / total)
    cy = float((yy * f0).sum() / total)
    sx = float(np.sqrt(((xx - cx) ** 2 * f0).sum() / total))
    sy = float(np.sqrt(((yy - cy) ** 2 * f0).sum() / total))

    noise = float(f.std()) or 1.0
    peak = float(f0.max())
    snr = peak / noise

    return {
        "cent_x": cx, "cent_y": cy,
        "sigma_x": sx, "sigma_y": sy,
        "snr": snr, "peak": peak, "background": bg,
    }


def compute_sharpness(frame) -> float:
    """Laplacian variance — 작을수록 흐림, 클수록 선명.

    AutoFocus / KIMM Z 스캔의 sharpness 메트릭으로 사용.
    """
    f = _to_gray2d(frame).astype(np.float32, copy=False)
    if f.shape[0] < 3 or f.shape[1] < 3:
        return 0.0
    lap = (
        f[1:-1, 2:] + f[1:-1, :-2] +
        f[2:, 1:-1] + f[:-2, 1:-1] -
        4.0 * f[1:-1, 1:-1]
    )
    return float(lap.var())


def make_thumbnail_rgb(frame, w: int = 80, h: int = 60) -> np.ndarray:
    """frame → (h, w, 3) uint8 — auto-stretch 후 nearest 다운샘플.

    cv2 없이 numpy 만 사용 (cv2 있으면 INTER_AREA 가 더 깔끔하나 외부 의존 회피).
    """
    f = _to_gray2d(frame)
    mi, ma = float(f.min()), float(f.max())
    if ma > mi:
        disp = ((f - mi) / (ma - mi) * 255.0).astype(np.uint8)
    else:
        disp = np.zeros_like(f, dtype=np.uint8)

    H, W = disp.shape
    if W == 0 or H == 0:
        return np.zeros((h, w, 3), dtype=np.uint8)

    scale = min(w / W, h / H)
    nw, nh = max(1, int(W * scale)), max(1, int(H * scale))
    # nearest neighbor 다운샘플
    ys = (np.linspace(0, H - 1, nh)).astype(np.int32)
    xs = (np.linspace(0, W - 1, nw)).astype(np.int32)
    small = disp[ys[:, None], xs[None, :]]

    canvas = np.zeros((h, w), dtype=np.uint8)
    y0 = (h - nh) // 2
    x0 = (w - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = small
    return np.stack([canvas, canvas, canvas], axis=-1)


# Process functions to plug into _ScanWorkerBase ────────────────────────────

def mirror_centroid_process_fn(frame, point, idx) -> Optional[dict]:
    """Mirror scan 용 — centroid stats 를 반환. point=(motor, target_steps).

    da_table 행 구성에 필요한 cent_x/cent_y/sigma_x/sigma_y/snr 만 채움.
    M1-M4 위치는 main_tab 에서 hub 로 별도 조회.
    """
    try:
        return compute_centroid_stats(frame)
    except Exception:
        return None


def kimm_sharpness_process_fn(frame, point, idx) -> Optional[dict]:
    """KIMM Z 스캔 용 — sharpness 계산. point=z(µm)."""
    try:
        return {"sharpness": compute_sharpness(frame), "z": float(point)}
    except Exception:
        return None
