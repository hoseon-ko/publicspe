"""
ui/kinematic/kinematic_scan_worker.py
ACS 6축 키네마틱 스테이지 스캔 워커.

동작 순서 (각 스텝):
  1. 6DOF 목표 계산 (스캔 축만 변경, 나머지 고정)
  2. KinematicCalc → CalPos 변환 + 인터락 검사
  3. ACS 스테이지 이동 (dry_run이면 실제 이동 없음)
  4. settle_ms 대기
  5. 카메라 스냅
  6. 선명도 측정
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from core.motor.kinematic_calc import KinematicCalc

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


def _sharpness(img: np.ndarray, metric: str, roi: Optional[tuple] = None) -> float:
    if roi:
        x0, y0, x1, y1 = roi
        img = img[y0:y1, x0:x1]
    if img.size == 0:
        return 0.0

    f = img.astype(np.float32)
    if metric == "laplacian":
        if _CV2_OK:
            return float(cv2.Laplacian(f, cv2.CV_64F).var())
        return float(f.std())
    elif metric == "contrast":
        return float(f.std())
    elif metric == "tenengrad":
        if _CV2_OK:
            gx = cv2.Sobel(f, cv2.CV_64F, 1, 0)
            gy = cv2.Sobel(f, cv2.CV_64F, 0, 1)
            return float((gx**2 + gy**2).mean())
        return float(f.std())
    elif metric == "brenner":
        return float(((f[2:] - f[:-2])**2).mean())
    return float(f.std())


# 스캔 축 인덱스 → (trans/rotate 배열 인덱스, 단위)
_AXIS_INFO = {
    "Tx": (0, "trans", "mm"),
    "Ty": (1, "trans", "mm"),
    "Tz": (2, "trans", "mm"),
    "Rx": (0, "rotate", "mrad"),
    "Ry": (1, "rotate", "mrad"),
    "Rz": (2, "rotate", "mrad"),
}


class KinematicScanWorker(QThread):
    """6DOF 스캔 워커."""

    step_done = pyqtSignal(int, int, float, float, object)
    # (step, total, position_value, sharpness, frame_ndarray)

    finished  = pyqtSignal(float, float)   # (best_pos, best_sharpness)
    error     = pyqtSignal(str)
    log       = pyqtSignal(str)

    def __init__(
        self,
        camera,
        acs_ctrl,
        scan_axis: str,           # "Tx"|"Ty"|"Tz"|"Rx"|"Ry"|"Rz"
        positions: list[float],   # 스캔 위치 목록 (mm or mrad)
        fixed_dof: dict,          # {"Tx":0,"Ty":0,"Tz":0,"Rx":0,"Ry":0,"Rz":0} 고정값
        calc: KinematicCalc,
        metric: str = "laplacian",
        settle_ms: int = 200,
        avg_frames: int = 1,
        roi: Optional[tuple] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._cam       = camera
        self._ctrl      = acs_ctrl
        self._axis      = scan_axis
        self._positions = positions
        self._fixed     = fixed_dof
        self._calc      = calc
        self._metric    = metric
        self._settle_ms = settle_ms
        self._avg       = avg_frames
        self._roi       = roi
        self._stop      = False

    def request_stop(self):
        self._stop = True

    def run(self):
        total = len(self._positions)
        ax_idx, ax_type, ax_unit = _AXIS_INFO[self._axis]

        results: list[tuple[float, float]] = []   # (pos, sharpness)

        for step, pos in enumerate(self._positions, 1):
            if self._stop:
                self.log.emit("스캔 중단됨")
                break

            # 6DOF 목표 구성
            trans  = [self._fixed.get(k, 0.0) for k in ("Tx", "Ty", "Tz")]
            rotate = [self._fixed.get(k, 0.0) for k in ("Rx", "Ry", "Rz")]
            if ax_type == "trans":
                trans[ax_idx] = pos
            else:
                rotate[ax_idx] = pos

            # 키네마틱 계산
            cal_pos, _, ok, violations = self._calc.calculate(trans, rotate)
            if cal_pos is None or not ok:
                msg = f"Step {step}: 인터락 위반 — {', '.join(violations[:2])}"
                self.log.emit(f"⚠ {msg}")
                continue

            # 이동: 6축을 모두 wait=False로 명령 발행 후 동시 시작
            # (개별 WaitMotionEnd를 직렬로 부르면 6배 느려지므로 settle_ms 일괄 대기로 대체)
            if self._ctrl is not None:
                dry = getattr(self._ctrl, "dry_run", False)
                try:
                    for i, target in enumerate(cal_pos):
                        self._ctrl.move_to(i, float(target), wait=False)
                except Exception as e:
                    self.error.emit(f"이동 오류: {e}")
                    return
            else:
                # ctrl이 없는 경우는 카메라 단독 테스트 모드 — dry로 간주
                dry = True

            # 6축 동시 정착 시간 (사용자 설정값 — 보통 200~500 ms)
            if self._settle_ms > 0:
                self.msleep(self._settle_ms)

            # 스냅
            try:
                frames = []
                for _ in range(max(1, self._avg)):
                    raw = self._cam.snap()
                    frames.append(np.asarray(raw, dtype=np.float32))
                frame = np.mean(frames, axis=0).astype(frames[0].dtype)
            except Exception as e:
                self.error.emit(f"카메라 오류: {e}")
                return

            sh = _sharpness(frame, self._metric, self._roi)
            results.append((pos, sh))
            self.step_done.emit(step, total, pos, sh, frame)
            dry_tag = "  [DRY]" if dry else ""
            self.log.emit(
                f"Step {step}/{total}  {self._axis}={pos:+.4f}{ax_unit}  S={sh:.2f}{dry_tag}"
            )

        if not results:
            self.error.emit(
                "유효한 스캔 결과 없음 — 모든 스텝에서 인터락 위반 또는 중단됨"
            )
            return

        # results = [(pos, sh), ...]에서 sh가 최대인 항목을 best로 선정
        best_pos, best_sh = max(results, key=lambda x: x[1])
        self.finished.emit(best_pos, best_sh)
