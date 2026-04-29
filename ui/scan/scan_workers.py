"""
ui/scan/scan_workers.py
스캔/캘리브레이션 QThread 워커 및 관련 헬퍼.
ScanTab UI와 분리하여 비즈니스 로직만 담는다.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from core.image_processor import ImageProcessor, TemporalMode
from core.spe_writer import save_spe

try:
    from core.camera.picamp import PicamCamera as _PicamCamera
except Exception:
    _PicamCamera = None


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _draw_centroid_cross(rgb: np.ndarray, cx: float, cy: float,
                          size: int = 20) -> np.ndarray:
    """RGB 배열에 청록색 십자 마커를 그린 복사본을 반환."""
    out = rgb.copy()
    h, w = out.shape[:2]
    x, y = int(round(cx)), int(round(cy))
    color = (0, 220, 180)
    x1, x2 = max(0, x - size), min(w, x + size + 1)
    y1, y2 = max(0, y - 1),    min(h, y + 2)
    out[y1:y2, x1:x2] = color
    x1, x2 = max(0, x - 1),    min(w, x + 2)
    y1, y2 = max(0, y - size), min(h, y + size + 1)
    out[y1:y2, x1:x2] = color
    for dy in (-size, size):
        for dx in (-2, -1, 0, 1, 2):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                out[ny, nx] = color
    for dx in (-size, size):
        for dy in (-2, -1, 0, 1, 2):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                out[ny, nx] = color
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 캘리브레이션 워커
# ─────────────────────────────────────────────────────────────────────────────

class _CalibWorker(QThread):
    """
    각 모터 단독 전진/후진 → centroid 변위 측정 → 방향벡터 + 가중치 비율 계산.

    결과 dict:
      {motor_num: {'fwd': {'dx','dy','mag','angle'},
                   'bwd': {'dx','dy','mag','angle'},
                   'weight_adj': float}}  # bwd_weight *= weight_adj 로 수정
    """
    log_message  = pyqtSignal(str)
    progress     = pyqtSignal(int, int)
    result_ready = pyqtSignal(dict)

    def __init__(self, cam, motor_panel, params: dict, parent=None):
        super().__init__(parent)
        self._cam         = cam
        self._motor       = motor_panel
        self._calib_steps = params["calib_steps"]
        self._settle_ms   = params["settle_ms"]
        self._motors      = params["motors"]   # e.g. [1, 2, 3]
        self._proc        = ImageProcessor()
        self._proc.centroid_enabled = True
        self._proc.temporal_mode = TemporalMode.SINGLE
        self._stop        = False

    def request_stop(self):
        self._stop = True

    def _snap_cx_cy(self):
        """snap + centroid 반환. 실패 시 (None, None)."""
        try:
            raw = np.asarray(self._cam.snap())
            r = self._proc.process(raw)
            return r.centroid_x, r.centroid_y
        except Exception:
            return None, None

    def run(self):
        total = 1 + len(self._motors) * 2  # baseline + N × (fwd + bwd)
        step  = 0

        self.log_message.emit("📍 기준 위치 스냅...")
        bx, by = self._snap_cx_cy()
        step += 1; self.progress.emit(step, total)
        if bx is None:
            self.log_message.emit("❌ centroid 측정 실패 — 이미지 프로세서 설정 확인")
            return
        self.log_message.emit(f"   기준 centroid: ({bx:.2f}, {by:.2f})")

        results = {}
        for motor_num in self._motors:
            if self._stop:
                break

            res = {}
            self.log_message.emit(f"── M{motor_num} 캘리브레이션 ──")

            # ── 전진 ──────────────────────────────────────────────────
            self.log_message.emit(f"  M{motor_num} +{self._calib_steps} steps →")
            self._motor.move(motor_num, self._calib_steps)
            self.msleep(self._settle_ms)
            fx, fy = self._snap_cx_cy()
            step += 1; self.progress.emit(step, total)

            if fx is not None:
                dx, dy = fx - bx, fy - by
                mag    = (dx**2 + dy**2) ** 0.5
                angle  = float(np.degrees(np.arctan2(dy, dx)))
                res["fwd"] = {"dx": dx, "dy": dy, "mag": mag, "angle": angle}
                self.log_message.emit(
                    f"    Δ({dx:+.2f}, {dy:+.2f})  {mag:.2f}px  {angle:.1f}°"
                )
            else:
                self.log_message.emit("    ❌ centroid 없음")

            # ── 후진 (원점 복귀) ──────────────────────────────────────
            self.log_message.emit(f"  M{motor_num} -{self._calib_steps} steps ←")
            self._motor.move(motor_num, -self._calib_steps)
            self.msleep(self._settle_ms)
            rx, ry = self._snap_cx_cy()
            step += 1; self.progress.emit(step, total)

            if rx is not None:
                dx2, dy2 = rx - bx, ry - by
                mag2   = (dx2**2 + dy2**2) ** 0.5
                angle2 = float(np.degrees(np.arctan2(dy2, dx2)))
                res["bwd"] = {"dx": dx2, "dy": dy2, "mag": mag2, "angle": angle2}
                self.log_message.emit(
                    f"    Δ({dx2:+.2f}, {dy2:+.2f})  {mag2:.2f}px  {angle2:.1f}°"
                )
                residual = mag2
                self.log_message.emit(f"    잔류 오차: {residual:.2f}px")
            else:
                self.log_message.emit("    ❌ centroid 없음")

            # ── 가중치 보정 계산 ──────────────────────────────────────
            if "fwd" in res and "bwd" in res:
                fwd_mag = res["fwd"]["mag"]
                bwd_mag = res["bwd"]["mag"]
                if bwd_mag > 0.5:
                    adj = fwd_mag / bwd_mag
                    res["weight_adj"] = adj
                    self.log_message.emit(
                        f"  → 전진/후진 크기 비: {adj:.4f}  "
                        f"(bwd_weight × {adj:.4f} 권장)"
                    )
                else:
                    self.log_message.emit("  ⚠️ 후진 변위 너무 작음 — 계산 불가")

            results[motor_num] = res

        self.log_message.emit("✅ 캘리브레이션 완료")
        self.result_ready.emit(results)


# ─────────────────────────────────────────────────────────────────────────────
# 스캔 워커
# ─────────────────────────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    """
    Snap → 분석 → 저장 → 이동 → 반복.
    각 스텝마다 step_done 시그널로 결과 전달.
    """
    step_done   = pyqtSignal(int, object, list, str)  # (idx, ProcessedFrame, positions, spe_path)
    progress    = pyqtSignal(int, int)                # (current, total)
    log_message = pyqtSignal(str)
    finished    = pyqtSignal(str)                     # CSV 요약 경로
    error       = pyqtSignal(str)

    def __init__(self, cam, motor_panel, params: dict, proc=None, parent=None):
        super().__init__(parent)
        self._cam         = cam
        self._motor       = motor_panel
        self._motor_num   = params["motor_num"]
        self._steps_move  = params["steps_move"]
        self._num_steps   = params["num_steps"]
        self._settle_ms   = params["settle_ms"]
        self._save_dir    = params["save_dir"]
        self._scan_name   = params["scan_name"]
        self._flush_snaps = params.get("flush_snaps", 0)
        self._proc        = proc if proc is not None else ImageProcessor()
        self._proc.centroid_enabled = True
        self._proc.temporal_mode = TemporalMode.SINGLE
        self._stop        = False
        self._records: list = []

    def request_stop(self):
        self._stop = True

    def run(self):
        os.makedirs(self._save_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(self._save_dir, f"{self._scan_name}_{ts}_summary.csv")

        cam_name = type(self._cam).__name__.replace("Camera", "")

        for i in range(self._num_steps):
            if self._stop:
                self.log_message.emit("■ 스캔 중단됨")
                break

            # ── Snap ──────────────────────────────────────────────────
            try:
                raw = np.asarray(self._cam.snap())
            except Exception as e:
                self.error.emit(f"Step {i+1} 촬영 실패: {e}")
                break

            # ── 분석 ──────────────────────────────────────────────────
            result = self._proc.process(raw)

            # ── 모터 위치 읽기 ─────────────────────────────────────────
            positions = self._motor.get_positions() if self._motor else [None]*4
            _pos = [p if p is not None else 0 for p in positions]
            _cx = f"{result.centroid_x:.3f}" if result.centroid_x is not None else "N/A"
            _cy = f"{result.centroid_y:.3f}" if result.centroid_y is not None else "N/A"

            # ── exposure 읽기 (메타데이터용) ───────────────────────────
            try:
                exp_ms = self._cam.get_exposure_ms()
            except Exception:
                exp_ms = 0.0

            stem = f"{self._scan_name}_{ts}_step{i+1:04d}"

            # ── SPE 저장 ──────────────────────────────────────────────
            spe_path = os.path.join(self._save_dir, stem + ".spe")
            _scan_extra = {
                "Scan": {
                    "ScanName":    self._scan_name,
                    "StepIndex":   str(i + 1),
                    "TotalSteps":  str(self._num_steps),
                    "MotorAxis":   f"M{self._motor_num}",
                    "StepsPerMove": str(self._steps_move),
                },
                "MotorPositions": {
                    "M1": str(_pos[0]),
                    "M2": str(_pos[1]),
                    "M3": str(_pos[2]),
                    "M4": str(_pos[3]),
                },
                "ImageAnalysis": {
                    "CentroidX":  _cx,
                    "CentroidY":  _cy,
                    "Brightness": str(result.brightness),
                    "SNR":        f"{result.snr:.3f}",
                    "FrameMean":  f"{result.frame_mean:.3f}",
                    "Saturated":  "true" if result.saturated else "false",
                    "SatRatio":   f"{result.sat_ratio:.6f}",
                },
            }
            try:
                if _PicamCamera is not None and isinstance(self._cam, _PicamCamera):
                    self._cam.save_as_spe(
                        spe_path, raw,
                        exposure_ms=exp_ms,
                        extra_metadata=_scan_extra,
                    )
                else:
                    save_spe(
                        spe_path, raw,
                        camera_name=cam_name,
                        exposure_ms=exp_ms,
                        creator="ScanTab",
                        extra_metadata=_scan_extra,
                    )
            except Exception as e:
                self.log_message.emit(f"⚠️ SPE 저장 오류: {e}")
                spe_path = ""

            # ── 이미지 저장 ───────────────────────────────────────────
            try:
                import cv2 as _cv2
                raw_img_path = os.path.join(self._save_dir, stem + "_raw.png")
                _cv2.imwrite(raw_img_path, raw)
                disp = result.display.copy()
                if disp.ndim == 2:
                    disp_bgr = _cv2.cvtColor(disp, _cv2.COLOR_GRAY2BGR)
                else:
                    disp_bgr = disp.copy()
                if result.has_centroid:
                    ix = int(round(result.centroid_x))
                    iy = int(round(result.centroid_y))
                    _cv2.drawMarker(disp_bgr, (ix, iy), (0, 220, 180),
                                    _cv2.MARKER_CROSS, 40, 2)
                    _cv2.putText(disp_bgr,
                                 f"({result.centroid_x:.1f},{result.centroid_y:.1f})",
                                 (ix + 8, iy - 8), _cv2.FONT_HERSHEY_SIMPLEX,
                                 0.5, (0, 220, 180), 1)
                disp_img_path = os.path.join(self._save_dir, stem + "_disp.png")
                _cv2.imwrite(disp_img_path, disp_bgr)
            except ImportError:
                raw_img_path = ""
                disp_img_path = ""
                self.log_message.emit("⚠️ OpenCV 없음 — 이미지 파일 저장 생략")
            except Exception as e:
                self.log_message.emit(f"⚠️ 이미지 저장 오류: {e}")
                raw_img_path = ""
                disp_img_path = ""

            # ── CSV 기록 ──────────────────────────────────────────────
            self._records.append({
                "step": i + 1,
                "M1": _pos[0], "M2": _pos[1], "M3": _pos[2], "M4": _pos[3],
                "centroid_x": result.centroid_x,
                "centroid_y": result.centroid_y,
                "brightness": result.brightness,
                "snr":        result.snr,
                "frame_mean": result.frame_mean,
                "spe_file":   os.path.basename(spe_path),
                "raw_img":    os.path.basename(raw_img_path),
                "disp_img":   os.path.basename(disp_img_path),
            })

            self.step_done.emit(i, result, positions, spe_path)
            self.progress.emit(i + 1, self._num_steps)
            self.log_message.emit(
                f"✅ Step {i+1}/{self._num_steps}  "
                f"Centroid=({_cx}, {_cy})  "
                f"M{self._motor_num}={_pos[self._motor_num-1]}"
            )

            # ── 모터 이동 (마지막 스텝 제외) ──────────────────────────
            if i < self._num_steps - 1 and not self._stop:
                ok = self._motor.move(self._motor_num, self._steps_move) \
                     if self._motor else False
                if not ok:
                    self.log_message.emit(f"⚠️ M{self._motor_num} 이동 실패")
                self.msleep(self._settle_ms)
                for _ in range(self._flush_snaps):
                    try:
                        self._cam.snap()
                    except Exception:
                        pass

        # ── CSV 저장 ──────────────────────────────────────────────────
        if self._records:
            try:
                with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.DictWriter(f, fieldnames=list(self._records[0].keys()))
                    w.writeheader()
                    w.writerows(self._records)
                self.finished.emit(csv_path)
            except Exception as e:
                self.error.emit(f"CSV 저장 오류: {e}")
        else:
            self.finished.emit("")
