"""
ui/autofocus/af_worker.py
AutoFocus 스캔 워커 — 실제 하드웨어 / SIM 공통.

Z 시퀀스를 순회하면서 카메라 스냅 → 선예도 계산 → step_done 시그널 방출.
완료 후 Best Z를 찾아 finished 시그널 방출.
"""
from __future__ import annotations

import time
from typing import Optional, List

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


# ─────────────────────────────────────────────────────────────────────────────
# 선예도(Sharpness) 메트릭
# ─────────────────────────────────────────────────────────────────────────────

def _to_u16(img: np.ndarray) -> np.ndarray:
    """OpenCV 필터 호환: uint16 2D 배열로 변환."""
    if img.ndim == 3:
        img = img[..., 0]
    return img.astype(np.uint16)


def sharpness_laplacian(img: np.ndarray) -> float:
    """Laplacian Variance.
    OpenCV 4.x: uint16 입력 → CV_32F 출력 조합만 안정적."""
    try:
        import cv2
        # uint16 → CV_32F : 지원되는 조합
        lap = cv2.Laplacian(_to_u16(img), cv2.CV_32F)
        return float(lap.var())
    except Exception:
        # fallback: numpy 차분
        f = img.astype(np.float32)
        lap = (f[1:-1, 1:-1] * -4
               + f[:-2, 1:-1] + f[2:, 1:-1]
               + f[1:-1, :-2] + f[1:-1, 2:])
        return float(lap.var())


def sharpness_contrast(img: np.ndarray) -> float:
    """Contrast (표준편차)."""
    return float(img.astype(np.float64).std())


def sharpness_tenengrad(img: np.ndarray) -> float:
    """Tenengrad (Sobel 에너지).
    uint16 → CV_32F 조합 사용."""
    try:
        import cv2
        u = _to_u16(img)
        sx = cv2.Sobel(u, cv2.CV_32F, 1, 0, ksize=3)
        sy = cv2.Sobel(u, cv2.CV_32F, 0, 1, ksize=3)
        return float((sx ** 2 + sy ** 2).mean())
    except Exception:
        return sharpness_contrast(img)


def sharpness_brenner(img: np.ndarray) -> float:
    """Brenner gradient."""
    f = img.astype(np.float64)
    diff = f[:, 2:] - f[:, :-2]
    return float((diff ** 2).sum())


_METRIC_FN = {
    "laplacian":  sharpness_laplacian,
    "contrast":   sharpness_contrast,
    "tenengrad":  sharpness_tenengrad,
    "brenner":    sharpness_brenner,
}


# ─────────────────────────────────────────────────────────────────────────────
# AutoFocusWorker
# ─────────────────────────────────────────────────────────────────────────────

class AutoFocusWorker(QThread):
    """
    Z 스캔 오토포커스 워커.

    시그널:
        step_done(step, total, z, sharpness, frame)
        finished(best_z, best_sharpness)
        error(message)
        log(message)
    """

    step_done = pyqtSignal(int, int, float, float, object)   # frame은 ndarray
    finished  = pyqtSignal(float, float)
    error     = pyqtSignal(str)
    log       = pyqtSignal(str)

    def __init__(
        self,
        camera,                            # snap() + get_exposure_ms() 인터페이스
        kimm_ctrl=None,                    # move_to_z(z) 인터페이스 (None = SIM)
        *,
        z_positions: List[float],          # 이동할 Z 좌표 목록 (µm)
        metric:    str   = "laplacian",
        settle_ms: int   = 200,
        avg_frames: int  = 1,
        sim_mode:  bool  = False,          # True면 kimm 이동 스킵
        roi_rect:  Optional[tuple[int, int, int, int]] = None,
        save_frames: bool = False,
        save_dir:    Optional[str] = None,
        rotation_k:  int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._cam         = camera
        self._kimm        = kimm_ctrl
        self._z_positions = list(z_positions)
        self._metric_fn   = _METRIC_FN.get(metric, sharpness_laplacian)
        self._settle_ms   = settle_ms
        self._avg_frames  = max(1, avg_frames)
        self._sim_mode    = sim_mode
        self._roi_rect    = roi_rect
        self._rotation_k  = rotation_k
        self._stop_req    = False
        self._frames: List[np.ndarray] = []

    def request_stop(self):
        self._stop_req = True

    # ── 메인 루프 ─────────────────────────────────────────────────────

    def run(self):
        z_list  = self._z_positions
        total   = len(z_list)
        z_vals:  List[float] = []
        sh_vals: List[float] = []

        for step, z in enumerate(z_list, 1):
            if self._stop_req:
                self.log.emit("AF 중단 요청")
                return

            # ── Z 이동 ──────────────────────────────────────────────
            if self._sim_mode:
                # SimAFCamera에 현재 Z 전달
                if hasattr(self._cam, "set_z"):
                    self._cam.set_z(z)
            else:
                if self._kimm is None:
                    self.error.emit("KIMM 컨트롤러 미연결")
                    return
                try:
                    ok = self._kimm.move_to_z(z)
                    if not ok:
                        self.error.emit(f"Z 이동 실패 (Move Done 타임아웃): {z:+.2f} µm")
                        return
                except Exception as e:
                    self.error.emit(f"Z 이동 예외 발생: {e}")
                    return

            # ── Settle 대기 ──────────────────────────────────────────
            if self._settle_ms > 0:
                t0 = time.perf_counter()
                while (time.perf_counter() - t0) * 1000 < self._settle_ms:
                    if self._stop_req:
                        return
                    time.sleep(0.01)

            # ── 스냅 + 평균화 ────────────────────────────────────────
            try:
                frames = []
                for _ in range(self._avg_frames):
                    f = self._cam.snap()
                    frames.append(f.astype(np.float32))
                frame_avg = np.mean(frames, axis=0).astype(np.uint16) \
                    if len(frames) > 1 else frames[0].astype(np.uint16)
            except Exception as e:
                self.error.emit(f"스냅 실패 (step {step}): {e}")
                return

            # ── 선예도 계산 ──────────────────────────────────────────
            metric_frame = frame_avg
            # 뷰어와 좌표계를 맞추기 위해 회전 적용
            if self._rotation_k:
                metric_frame = np.rot90(metric_frame, k=self._rotation_k)

            if self._roi_rect is not None:
                x0, y0, x1, y1 = self._roi_rect
                h, w = metric_frame.shape[:2]
                x0, x1 = max(0, min(x0, w)), max(0, min(x1, w))
                y0, y1 = max(0, min(y0, h)), max(0, min(y1, h))
                if x0 < x1 and y0 < y1:
                    metric_frame = frame_avg[y0:y1, x0:x1]

            sh = self._metric_fn(metric_frame)
            z_vals.append(z)
            sh_vals.append(sh)

            self.step_done.emit(step, total, z, sh, frame_avg)

        # ── Best Z 결정 ──────────────────────────────────────────────
        if not sh_vals:
            self.error.emit("측정값 없음")
            return

        best_idx = int(np.argmax(sh_vals))
        best_z   = z_vals[best_idx]
        best_sh  = sh_vals[best_idx]
        self.log.emit(
            f"Best Z = {best_z:+.3f} µm  (step {best_idx+1}/{total},"
            f" sharpness = {best_sh:.1f})"
        )
        self.finished.emit(best_z, best_sh)
