"""
ui/scan/scan_tab.py
자동 스캔 탭 — 카메라 스냅 + 모터 이동 + 데이터 저장.

흐름:
  (현재 위치에서) Snap → 분석 → 저장 → 모터 이동 → 정착 대기 → 반복
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Optional

import numpy as np
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QSettings
)
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QGroupBox, QLabel, QPushButton, QSpinBox,
    QComboBox, QLineEdit, QFileDialog,
    QProgressBar, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea,
    QCheckBox, QListWidget, QListWidgetItem,
)
from PyQt6.QtGui import QIcon, QPixmap, QImage

from core.image_processor import ImageProcessor
from core.spe_writer import save_spe
from ui.image_viewer import ImageViewer
from ui.plot_panel import PlotPanel

# 스타일 공통
_BTN_PRIMARY = """
    QPushButton {
        background: #0d2820; color: #4ecdc4;
        border: 1px solid #4ecdc4; border-radius: 4px;
        font-family: 'Courier New'; font-weight: bold;
        font-size: 12px; padding: 6px 14px;
    }
    QPushButton:hover { background: #1a4838; }
    QPushButton:disabled { color: #1a2840; background: #080e1e; border-color: #0a1828; }
"""
_BTN_DANGER = """
    QPushButton {
        background: #200808; color: #e94560;
        border: 1px solid #e94560; border-radius: 4px;
        font-family: 'Courier New'; font-weight: bold;
        font-size: 12px; padding: 6px 14px;
    }
    QPushButton:hover { background: #3a1020; }
    QPushButton:disabled { color: #2a1010; background: #100404; border-color: #200808; }
"""
_SPIN_STYLE = """
    QSpinBox {
        background: #080e1e; border: 1px solid #0f3460;
        color: #c0d0ff; border-radius: 3px;
        font-family: 'Courier New'; font-size: 11px; padding: 2px 4px;
    }
"""
_GRP = """
    QGroupBox {{
        border: 1px solid {c}; border-radius: 6px;
        margin-top: 10px; font-family: 'Courier New';
        font-size: 11px; color: {c}; letter-spacing: 1px; font-weight: bold;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
"""


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

        # 기준 centroid
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
                # 잔류 오차: 기준으로 돌아오지 못한 거리
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

    def __init__(self, cam, motor_panel, params: dict, parent=None):
        super().__init__(parent)
        self._cam         = cam
        self._motor       = motor_panel
        self._motor_num   = params["motor_num"]
        self._steps_move  = params["steps_move"]
        self._num_steps   = params["num_steps"]
        self._settle_ms   = params["settle_ms"]
        self._save_dir    = params["save_dir"]
        self._scan_name   = params["scan_name"]
        self._proc        = ImageProcessor()
        self._proc.centroid_enabled = True
        self._stop        = False
        self._records: list = []   # CSV 누적

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

            # ── SPE 저장 (raw 데이터 + XML 메타데이터) ─────────────────
            spe_path = os.path.join(self._save_dir, stem + ".spe")
            try:
                save_spe(
                    spe_path,
                    raw,
                    camera_name=cam_name,
                    exposure_ms=exp_ms,
                    creator="ScanTab",
                    extra_metadata={
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
                    },
                )
            except Exception as e:
                self.log_message.emit(f"⚠️ SPE 저장 오류: {e}")
                spe_path = ""

            # ── 이미지 저장 (raw BMP + display PNG) ────────────────────
            try:
                import cv2 as _cv2
                # raw: 원본 16-bit 그대로
                raw_img_path = os.path.join(self._save_dir, stem + "_raw.png")
                _cv2.imwrite(raw_img_path, raw)
                # display: 8-bit 정규화 + centroid 마커 오버레이
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


# ─────────────────────────────────────────────────────────────────────────────
# Scan 탭
# ─────────────────────────────────────────────────────────────────────────────

class ScanTab(QWidget):
    scan_starting = pyqtSignal()   # → MainWindow → live_tab.stop_live()
    log_message   = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam          = None
        self._motor_panel  = None
        self._sim_cam      = None
        self._sim_motor    = None
        self._worker: Optional[_ScanWorker] = None
        self._calib_worker: Optional[_CalibWorker] = None
        self._scan_records: list = []
        self._image_list:   list = []   # 스텝별 raw ndarray 누적

        self._plot_x:  list = []
        self._plot_cx: list = []
        self._plot_cy: list = []
        self._build_ui()
        self._restore_settings()

    # ── Public API ────────────────────────────────────────────────────

    def set_shared_camera(self, cam):
        self._cam = cam
        cam_name = type(cam).__name__.replace("Camera", "")
        self._lbl_cam.setText(f"📷 {cam_name}  ● CONNECTED")
        self._lbl_cam.setStyleSheet("color: #4ecdc4; font-family: 'Courier New'; font-size: 11px;")
        self.btn_start.setEnabled(True)
        self.btn_calibrate.setEnabled(True)

    def clear_shared_camera(self):
        self._cam = None
        self._lbl_cam.setText("📷 카메라 없음")
        self._lbl_cam.setStyleSheet("color: #e94560; font-family: 'Courier New'; font-size: 11px;")
        self.btn_start.setEnabled(False)
        self.btn_calibrate.setEnabled(False)

    def set_motor_panel(self, motor_panel):
        """Live 탭의 MotorPanel 공유 — 위치 읽기 + 이동 명령."""
        self._motor_panel = motor_panel

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ── 좌측: 컨트롤 패널 ─────────────────────────────────────────
        ctrl_scroll = QScrollArea()
        ctrl_scroll.setWidgetResizable(True)
        ctrl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ctrl_scroll.setStyleSheet("QScrollArea { border: none; background: #0a0f1e; }")
        ctrl_widget = QWidget()
        ctrl_widget.setStyleSheet("background: #0a0f1e;")
        ctrl_layout = QVBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(8, 8, 8, 8)
        ctrl_layout.setSpacing(8)
        ctrl_scroll.setWidget(ctrl_widget)
        ctrl_scroll.setFixedWidth(290)

        # 카메라 상태
        grp_cam = QGroupBox("CAMERA")
        grp_cam.setStyleSheet(_GRP.format(c="#4ecdc4"))
        gc = QVBoxLayout(grp_cam)
        gc.setSpacing(5)
        self._lbl_cam = QLabel("📷 카메라 없음")
        self._lbl_cam.setStyleSheet("color: #e94560; font-family: 'Courier New'; font-size: 11px;")
        gc.addWidget(self._lbl_cam)

        self.btn_sim = QPushButton("▷  SIM MODE")
        self.btn_sim.setToolTip("실 하드웨어 없이 가상 카메라+모터로 동작 검증")
        self.btn_sim.setStyleSheet("""
            QPushButton {
                background: #1a1a0a; color: #ffe66d;
                border: 1px solid #ffe66d; border-radius: 4px;
                font-family: 'Courier New'; font-weight: bold;
                font-size: 11px; padding: 4px 10px;
            }
            QPushButton:hover  { background: #2a2a10; }
            QPushButton:checked {
                background: #2a2800; color: #ffcc00;
                border-color: #ffcc00;
            }
        """)
        self.btn_sim.setCheckable(True)
        self.btn_sim.clicked.connect(self._toggle_sim_mode)
        gc.addWidget(self.btn_sim)
        ctrl_layout.addWidget(grp_cam)

        # 스캔 파라미터
        grp_scan = QGroupBox("SCAN PARAMETERS")
        grp_scan.setStyleSheet(_GRP.format(c="#ffe66d"))
        gs = QVBoxLayout(grp_scan)
        gs.setSpacing(6)

        def _row(label, widget):
            r = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(100)
            lbl.setStyleSheet("color: #7080a0; font-family: 'Courier New'; font-size: 11px;")
            r.addWidget(lbl)
            r.addWidget(widget)
            return r

        # 모터 축 선택
        self.combo_motor = QComboBox()
        self.combo_motor.addItems(["M1", "M2", "M3", "M4"])
        self.combo_motor.setStyleSheet(
            "QComboBox { background:#080e1e; border:1px solid #0f3460; color:#c0d0ff;"
            "border-radius:3px; font-family:'Courier New'; font-size:11px; padding:2px 4px; }"
        )
        gs.addLayout(_row("이동 축:", self.combo_motor))

        # 스텝당 이동량
        self.spin_steps_move = QSpinBox()
        self.spin_steps_move.setRange(-999999, 999999)
        self.spin_steps_move.setValue(500)
        self.spin_steps_move.setStyleSheet(_SPIN_STYLE)
        gs.addLayout(_row("스텝/이동:", self.spin_steps_move))

        # 총 스텝 수
        self.spin_num_steps = QSpinBox()
        self.spin_num_steps.setRange(1, 10000)
        self.spin_num_steps.setValue(10)
        self.spin_num_steps.setStyleSheet(_SPIN_STYLE)
        gs.addLayout(_row("총 스텝 수:", self.spin_num_steps))

        # 정착 대기 시간
        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 60000)
        self.spin_settle.setValue(500)
        self.spin_settle.setSuffix(" ms")
        self.spin_settle.setStyleSheet(_SPIN_STYLE)
        gs.addLayout(_row("정착 대기:", self.spin_settle))

        ctrl_layout.addWidget(grp_scan)

        # 저장 경로
        grp_save = QGroupBox("SAVE")
        grp_save.setStyleSheet(_GRP.format(c="#a080ff"))
        gsv = QVBoxLayout(grp_save)

        self.edit_scan_name = QLineEdit("Scan")
        self.edit_scan_name.setPlaceholderText("스캔 이름")
        self.edit_scan_name.setStyleSheet(
            "QLineEdit { background:#080e1e; border:1px solid #0f3460; color:#c0d0ff;"
            "border-radius:3px; font-family:'Courier New'; font-size:11px; padding:2px 4px; }"
        )
        gsv.addWidget(self.edit_scan_name)

        dir_row = QHBoxLayout()
        self.edit_save_dir = QLineEdit("Scan_Data")
        self.edit_save_dir.setStyleSheet(
            "QLineEdit { background:#080e1e; border:1px solid #0f3460; color:#c0d0ff;"
            "border-radius:3px; font-family:'Courier New'; font-size:11px; padding:2px 4px; }"
        )
        btn_browse = QPushButton("…")
        btn_browse.setFixedWidth(28)
        btn_browse.setStyleSheet(
            "QPushButton { background:#0d1e38; color:#a0b0d0; border:1px solid #1a3060;"
            "border-radius:3px; font-size:12px; } QPushButton:hover { color:#4ecdc4; }"
        )
        btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.edit_save_dir)
        dir_row.addWidget(btn_browse)
        gsv.addLayout(dir_row)

        ctrl_layout.addWidget(grp_save)

        # 시작 / 정지
        self.btn_start = QPushButton("▶  START SCAN")
        self.btn_start.setStyleSheet(_BTN_PRIMARY)
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._start_scan)

        self.btn_stop = QPushButton("■  STOP")
        self.btn_stop.setStyleSheet(_BTN_DANGER)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_scan)

        ctrl_layout.addWidget(self.btn_start)
        ctrl_layout.addWidget(self.btn_stop)

        # 진행바
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar { background:#080e1e; border:1px solid #0f3460; border-radius:4px;
                color:#4ecdc4; font-family:'Courier New'; font-size:10px; }
            QProgressBar::chunk { background:#0d2820; border-radius:3px; }
        """)
        ctrl_layout.addWidget(self.progress_bar)

        self._lbl_progress = QLabel("—")
        self._lbl_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_progress.setStyleSheet(
            "color: #4a6a8a; font-family: 'Courier New'; font-size: 10px;"
        )
        ctrl_layout.addWidget(self._lbl_progress)

        # ── 프레임 분석 ────────────────────────────────────────────────
        grp_frame = QGroupBox("FRAME ANALYSIS")
        grp_frame.setStyleSheet(_GRP.format(c="#ff9f43"))
        gf = QVBoxLayout(grp_frame)
        gf.setSpacing(5)

        # 최대 보관 프레임 수
        self.spin_max_frames = QSpinBox()
        self.spin_max_frames.setRange(1, 9999)
        self.spin_max_frames.setValue(200)
        self.spin_max_frames.setSuffix(" frames")
        self.spin_max_frames.setStyleSheet(_SPIN_STYLE)
        self.spin_max_frames.setToolTip("메모리 보호: 초과 시 가장 오래된 프레임부터 삭제")
        gf.addLayout(_row("최대 보관:", self.spin_max_frames))


        # Frame A / B 선택
        ab_row = QHBoxLayout()
        for lbl_text, attr in (("A:", "spin_frame_a"), ("B:", "spin_frame_b")):
            l = QLabel(lbl_text)
            l.setFixedWidth(14)
            l.setStyleSheet("color:#7080a0; font-family:'Courier New'; font-size:11px;")
            sp = QSpinBox()
            sp.setRange(0, 9999)
            sp.setValue(0)
            sp.setStyleSheet(_SPIN_STYLE)
            setattr(self, attr, sp)
            ab_row.addWidget(l)
            ab_row.addWidget(sp)
        gf.addLayout(ab_row)

        # 표시 버튼
        btn_row_f1 = QHBoxLayout()
        self.btn_show_a = QPushButton("Show A")
        self.btn_show_b = QPushButton("Show B")
        for btn in (self.btn_show_a, self.btn_show_b):
            btn.setStyleSheet(_BTN_PRIMARY)
            btn_row_f1.addWidget(btn)
        gf.addLayout(btn_row_f1)

        btn_row_f2 = QHBoxLayout()
        self.btn_diff    = QPushButton("A − B")
        self.btn_absdiff = QPushButton("|A − B|")
        for btn in (self.btn_diff, self.btn_absdiff):
            btn.setStyleSheet(_BTN_PRIMARY)
            btn_row_f2.addWidget(btn)
        gf.addLayout(btn_row_f2)

        self.btn_show_a.clicked.connect(lambda: self._show_frame_idx(self.spin_frame_a.value()))
        self.btn_show_b.clicked.connect(lambda: self._show_frame_idx(self.spin_frame_b.value()))
        self.btn_diff.clicked.connect(self._show_diff)
        self.btn_absdiff.clicked.connect(self._show_abs_diff)
        ctrl_layout.addWidget(grp_frame)

        # ── 캘리브레이션 ───────────────────────────────────────────────
        grp_calib = QGroupBox("CALIBRATION")
        grp_calib.setStyleSheet(_GRP.format(c="#fd79a8"))
        gcal = QVBoxLayout(grp_calib)
        gcal.setSpacing(5)

        # 캘리브레이션 스텝 수
        self.spin_calib_steps = QSpinBox()
        self.spin_calib_steps.setRange(10, 999999)
        self.spin_calib_steps.setValue(1000)
        self.spin_calib_steps.setStyleSheet(_SPIN_STYLE)
        gcal.addLayout(_row("캘리브 스텝:", self.spin_calib_steps))

        # 대상 모터 선택 (체크박스)
        chk_row = QHBoxLayout()
        self._calib_chk = {}
        for mn in (1, 2, 3):
            chk = QCheckBox(f"M{mn}")
            chk.setChecked(True)
            chk.setStyleSheet(
                "QCheckBox { color:#c0d0ff; font-family:'Courier New'; font-size:11px; }"
            )
            self._calib_chk[mn] = chk
            chk_row.addWidget(chk)
        gcal.addLayout(chk_row)

        self.btn_calibrate = QPushButton("⚙  CALIBRATE")
        self.btn_calibrate.setStyleSheet(_BTN_PRIMARY)
        self.btn_calibrate.setEnabled(False)
        self.btn_calibrate.clicked.connect(self._start_calibration)
        gcal.addWidget(self.btn_calibrate)

        ctrl_layout.addWidget(grp_calib)

        ctrl_layout.addStretch()
        splitter.addWidget(ctrl_scroll)

        # ── 중앙+우측: 이미지 + 플롯 ──────────────────────────────────
        center_right = QSplitter(Qt.Orientation.Horizontal)

        # 이미지 뷰어
        self.image_viewer = ImageViewer()
        center_right.addWidget(self.image_viewer)

        # 우측: 플롯 + 로그 + 테이블 (세로 분할)
        right_widget = QWidget()
        right_widget.setStyleSheet("background: #0a0f1e;")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(4)

        # 이미지 썸네일 리스트
        lbl_frames = QLabel("CAPTURED FRAMES")
        lbl_frames.setStyleSheet(
            "color:#4ecdc4; font-family:'Courier New'; font-size:10px; "
            "font-weight:bold; letter-spacing:1px; padding:2px 0;"
        )
        right_layout.addWidget(lbl_frames)

        self._frame_list = QListWidget()
        self._frame_list.setIconSize(__import__('PyQt6.QtCore', fromlist=['QSize']).QSize(80, 60))
        self._frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self._frame_list.setWrapping(False)
        self._frame_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._frame_list.setFixedHeight(100)
        self._frame_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._frame_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frame_list.setStyleSheet("""
            QListWidget { background:#080e1e; border:1px solid #0f3460; color:#c0d0ff; }
            QListWidget::item { padding:2px; border:1px solid #0f2040; }
            QListWidget::item:selected { background:#1a3a60; border:1px solid #4ecdc4; }
        """)
        self._frame_list.currentRowChanged.connect(self._on_frame_list_select)
        right_layout.addWidget(self._frame_list)

        # 플롯 패널 (위치 vs centroid)
        self.plot_panel = PlotPanel("Centroid X/Y vs Motor Position")
        self.plot_panel.setMinimumHeight(200)
        right_layout.addWidget(self.plot_panel, 2)

        # 로그
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMaximumHeight(120)
        self.log_display.setStyleSheet(
            "QTextEdit { background:#080e1e; border:1px solid #0f3460;"
            "color:#00cc88; font-family:'Courier New'; font-size:10px; }"
        )
        right_layout.addWidget(self.log_display, 1)

        # 결과 테이블
        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels(
            ["Step", "M1", "M2", "M3", "M4", "CentX", "CentY", "SNR", "SPE"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setStyleSheet("""
            QTableWidget { background:#080e1e; gridline-color:#0f3460;
                color:#c0d0ff; font-family:'Courier New'; font-size:10px; border:none; }
            QHeaderView::section { background:#0f1729; color:#4ecdc4;
                border:1px solid #0f3460; font-family:'Courier New'; font-size:10px; }
            QTableWidget::item:selected { background:#1a3a60; }
        """)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right_layout.addWidget(self._table, 2)

        center_right.addWidget(right_widget)
        center_right.setSizes([700, 400])

        splitter.addWidget(center_right)
        splitter.setSizes([290, 1110])

    # ── 컨트롤 잠금 ──────────────────────────────────────────────────

    def _set_controls_locked(self, locked: bool):
        """동작 중 모든 파라미터/시작 버튼 잠금, 정지만 활성."""
        idle = not locked
        cam_ok = self._cam is not None
        motor_ok = self._motor_panel is not None and self._motor_panel.is_connected

        self.btn_start.setEnabled(idle and cam_ok)
        self.btn_stop.setEnabled(locked and (self._worker is not None))
        self.btn_calibrate.setEnabled(idle and cam_ok and motor_ok)
        self.btn_sim.setEnabled(idle)

        # 파라미터 위젯
        for w in (
            self.combo_motor,
            self.spin_steps_move, self.spin_num_steps, self.spin_settle,
            self.spin_max_frames, self.spin_calib_steps,
            self.spin_frame_a, self.spin_frame_b,
            self.edit_scan_name, self.edit_save_dir,
            self.btn_show_a, self.btn_show_b,
            self.btn_diff, self.btn_absdiff,
        ):
            w.setEnabled(idle)
        for chk in self._calib_chk.values():
            chk.setEnabled(idle)

    # ── 스캔 제어 ─────────────────────────────────────────────────────

    def _start_scan(self):
        if self._cam is None:
            self._log("❌ 카메라 연결 필요")
            return
        if self._worker and self._worker.isRunning():
            self._log("⚠️ 이미 스캔 중")
            return
        if self._calib_worker and self._calib_worker.isRunning():
            self._log("❌ 캘리브레이션 진행 중 — 완료 후 시작")
            return

        motor_num   = int(self.combo_motor.currentText()[1])
        steps_move  = self.spin_steps_move.value()
        num_steps   = self.spin_num_steps.value()
        settle_ms   = self.spin_settle.value()
        save_dir    = self.edit_save_dir.text().strip() or "Scan_Data"
        scan_name   = self.edit_scan_name.text().strip() or "Scan"

        if self._motor_panel and not self._motor_panel.is_connected:
            self._log("⚠️ Picomotor 연결 안 됨 — 위치 기록 없이 진행")

        self.scan_starting.emit()
        self._set_controls_locked(True)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(num_steps)
        self._table.setRowCount(0)
        self._frame_list.clear()
        self._scan_records.clear()
        self._image_list.clear()
        self._plot_x.clear()
        self._plot_cx.clear()
        self._plot_cy.clear()

        params = {
            "motor_num":   motor_num,
            "steps_move":  steps_move,
            "num_steps":   num_steps,
            "settle_ms":   settle_ms,
            "save_dir":    save_dir,
            "scan_name":   scan_name,
        }
        self._worker = _ScanWorker(self._cam, self._motor_panel, params)
        self._worker.step_done.connect(self._on_step_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._log)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()
        self._log(f"▶ 스캔 시작 — M{motor_num} × {num_steps} steps ({steps_move} step/move)")

    def _stop_scan(self):
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
        self._log("■ 정지 요청...")

    # ── 워커 콜백 ─────────────────────────────────────────────────────

    def _on_step_done(self, idx: int, result, positions: list, spe_path: str):
        # 이미지 리스트 누적 (상한 초과 시 가장 오래된 것 제거)
        max_frames = self.spin_max_frames.value()
        pos_snapshot = [p if p is not None else 0 for p in positions]
        self._image_list.append((idx, result.raw.copy(), pos_snapshot))
        if len(self._image_list) > max_frames:
            evicted_idx, _, _ = self._image_list.pop(0)
            self._frame_list.takeItem(0)
            if len(self._image_list) == max_frames:
                self._log(f"⚠️ 프레임 상한 {max_frames}개 도달 — RAM에서 제거 (SPE는 디스크에 유지)")

        # 프레임 스핀박스 최대값 갱신
        n = len(self._image_list) - 1
        self.spin_frame_a.setMaximum(n)
        self.spin_frame_b.setMaximum(n)

        # 썸네일 리스트 추가
        self._append_thumbnail(result.display, idx)

        # 이미지 표시
        disp = result.display
        if disp.ndim == 2:
            try:
                import cv2
                rgb = cv2.cvtColor(disp, cv2.COLOR_GRAY2RGB)
            except ImportError:
                rgb = np.stack([disp, disp, disp], axis=-1)
        else:
            rgb = disp
        self.image_viewer.set_live_frame(rgb, fit=(idx == 0))

        # 테이블 추가
        row = self._table.rowCount()
        self._table.insertRow(row)
        p = [p if p is not None else 0 for p in positions]
        cx = f"{result.centroid_x:.1f}" if result.centroid_x is not None else "—"
        cy = f"{result.centroid_y:.1f}" if result.centroid_y is not None else "—"
        vals = [
            str(idx + 1),
            str(p[0]), str(p[1]), str(p[2]), str(p[3]),
            cx, cy,
            f"{result.snr:.2f}",
            os.path.basename(spe_path),
        ]
        for col, v in enumerate(vals):
            self._table.setItem(row, col, QTableWidgetItem(v))
        self._table.scrollToBottom()

        # 플롯 업데이트 — centroid None이면 0으로 채워 길이 항상 일치
        motor_num = int(self.combo_motor.currentText()[1])
        self._plot_x.append(p[motor_num - 1])
        self._plot_cx.append(result.centroid_x if result.centroid_x is not None else 0.0)
        self._plot_cy.append(result.centroid_y if result.centroid_y is not None else 0.0)

        self.plot_panel.plot_two_lines(
            np.array(self._plot_cx),
            np.array(self._plot_cy),
            "Centroid X",
            "Centroid Y",
        )

    def _on_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)
        self._lbl_progress.setText(f"{current} / {total}")

    def _on_scan_finished(self, csv_path: str):
        self._worker = None
        self._set_controls_locked(False)
        if csv_path:
            self._log(f"✅ 스캔 완료 — CSV: {csv_path}")
        else:
            self._log("✅ 스캔 완료 (데이터 없음)")

    def _on_scan_error(self, msg: str):
        self._log(f"❌ {msg}")
        self._worker = None
        self._set_controls_locked(False)

    # ── 썸네일 ───────────────────────────────────────────────────────

    def _append_thumbnail(self, display: np.ndarray, step_idx: int):
        """display(uint8 2D/3D)를 80×60 썸네일로 QListWidget에 추가."""
        disp = display
        if disp.ndim == 2:
            disp = np.stack([disp, disp, disp], axis=-1)
        h, w = disp.shape[:2]
        thumb_w, thumb_h = 80, 60
        # 비율 유지 리사이즈
        scale = min(thumb_w / w, thumb_h / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        try:
            import cv2
            small = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
        except ImportError:
            small = disp[::max(1, h // nh), ::max(1, w // nw)][:nh, :nw]

        # 검은 배경 캔버스에 중앙 배치
        canvas = np.zeros((thumb_h, thumb_w, 3), dtype=np.uint8)
        y0 = (thumb_h - nh) // 2
        x0 = (thumb_w - nw) // 2
        canvas[y0:y0+nh, x0:x0+nw] = small[:, :, :3]

        img = QImage(canvas.tobytes(), thumb_w, thumb_h, thumb_w * 3, QImage.Format.Format_RGB888)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(img)), f"#{step_idx+1}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._frame_list.addItem(item)
        self._frame_list.scrollToItem(item)

    def _on_frame_list_select(self, row: int):
        """리스트에서 프레임 선택 → 이미지뷰어 + 스핀박스 동기화."""
        if row < 0 or row >= len(self._image_list):
            return
        self._show_frame_idx(row)
        # 스핀박스 B를 마지막 선택에 맞춤 (A는 항상 직전)
        self.spin_frame_b.setValue(row)
        if row > 0:
            self.spin_frame_a.setValue(row - 1)

    # ── 프레임 분석 ───────────────────────────────────────────────────

    def _show_frame_idx(self, idx: int):
        if not self._image_list:
            self._log("⚠️ 저장된 프레임 없음")
            return
        idx = max(0, min(idx, len(self._image_list) - 1))
        step_i, raw, pos = self._image_list[idx]
        disp = (raw >> 8).astype(np.uint8) if raw.dtype == np.uint16 else raw.astype(np.uint8)
        try:
            import cv2
            rgb = cv2.cvtColor(disp, cv2.COLOR_GRAY2RGB) if disp.ndim == 2 else disp.copy()
        except ImportError:
            rgb = np.stack([disp, disp, disp], axis=-1) if disp.ndim == 2 else disp.copy()
        self.image_viewer.set_live_frame(rgb, fit=False)
        self._log(
            f"🖼 Step#{step_i+1}  M1={pos[0]}  M2={pos[1]}  M3={pos[2]}  M4={pos[3]}"
        )

    def _show_diff(self):
        self._render_diff(absolute=False)

    def _show_abs_diff(self):
        self._render_diff(absolute=True)

    def _render_diff(self, absolute: bool):
        if len(self._image_list) < 2:
            self._log("⚠️ 비교할 프레임 2개 이상 필요")
            return
        a_idx = max(0, min(self.spin_frame_a.value(), len(self._image_list) - 1))
        b_idx = max(0, min(self.spin_frame_b.value(), len(self._image_list) - 1))
        if a_idx == b_idx:
            self._log("⚠️ A와 B가 같은 프레임")
            return

        step_a, raw_a, pos_a = self._image_list[a_idx]
        step_b, raw_b, pos_b = self._image_list[b_idx]
        a = raw_a.astype(np.float32)
        b = raw_b.astype(np.float32)
        dp = [pb - pa for pa, pb in zip(pos_a, pos_b)]
        self._log(
            f"A=Step#{step_a+1} → B=Step#{step_b+1}  "
            f"ΔM1={dp[0]:+d}  ΔM2={dp[1]:+d}  ΔM3={dp[2]:+d}  ΔM4={dp[3]:+d}"
        )
        diff = a - b  # 부호 있는 차이

        if absolute:
            arr = np.abs(diff)
            vmin, vmax = 0.0, float(arr.max()) or 1.0
            # 0~255 단순 정규화
            disp8 = ((arr - vmin) / (vmax - vmin) * 255).astype(np.uint8)
            try:
                import cv2
                rgb = cv2.cvtColor(disp8, cv2.COLOR_GRAY2RGB) if disp8.ndim == 2 else disp8.copy()
            except ImportError:
                rgb = np.stack([disp8, disp8, disp8], axis=-1)
            self._log(f"|A-B|  max={arr.max():.1f}  mean={arr.mean():.2f}")
        else:
            # diverging 컬러맵: 음수→파랑, 0→검정, 양수→빨강
            peak = float(max(abs(diff.min()), abs(diff.max()))) or 1.0
            norm = diff / peak  # -1 ~ +1
            r_ch = np.clip( norm * 255, 0, 255).astype(np.uint8)
            b_ch = np.clip(-norm * 255, 0, 255).astype(np.uint8)
            g_ch = np.zeros_like(r_ch)
            rgb = np.stack([r_ch, g_ch, b_ch], axis=-1)
            self._log(f"A-B  min={diff.min():.1f}  max={diff.max():.1f}  mean={diff.mean():.2f}")

        self.image_viewer.set_live_frame(rgb, fit=False)

    # ── 캘리브레이션 ──────────────────────────────────────────────────

    def _start_calibration(self):
        if self._cam is None:
            self._log("❌ 카메라 연결 필요")
            return
        if self._motor_panel is None or not self._motor_panel.is_connected:
            self._log("❌ 모터 연결 필요")
            return
        if self._calib_worker and self._calib_worker.isRunning():
            self._log("⚠️ 캘리브레이션 이미 진행 중")
            return
        if self._worker and self._worker.isRunning():
            self._log("❌ 스캔 진행 중 — 완료 후 시작")
            return

        motors = [mn for mn, chk in self._calib_chk.items() if chk.isChecked()]
        if not motors:
            self._log("⚠️ 캘리브레이션할 모터 선택 필요")
            return

        params = {
            "calib_steps": self.spin_calib_steps.value(),
            "settle_ms":   self.spin_settle.value(),
            "motors":      motors,
        }
        self._calib_worker = _CalibWorker(self._cam, self._motor_panel, params)
        self._calib_worker.log_message.connect(self._log)
        self._calib_worker.result_ready.connect(self._on_calib_result)
        self._set_controls_locked(True)
        self._calib_worker.start()
        self._log(f"⚙ 캘리브레이션 시작 — M{motors}  ±{params['calib_steps']} steps")

    def _on_calib_result(self, results: dict):
        self._calib_worker = None
        self._set_controls_locked(False)
        self._log("── 캘리브레이션 결과 ──")
        for motor_num, res in results.items():
            parts = [f"M{motor_num}:"]
            if "fwd" in res:
                f = res["fwd"]
                parts.append(f"FWD Δ({f['dx']:+.2f},{f['dy']:+.2f}) {f['mag']:.2f}px {f['angle']:.1f}°")
            if "bwd" in res:
                b = res["bwd"]
                parts.append(f"BWD Δ({b['dx']:+.2f},{b['dy']:+.2f}) {b['mag']:.2f}px {b['angle']:.1f}°")
            if "weight_adj" in res:
                parts.append(f"adj={res['weight_adj']:.4f}")
            self._log("  " + "  |  ".join(parts))
        self._calib_worker = None

    # ── 유틸 ─────────────────────────────────────────────────────────

    def _toggle_sim_mode(self, checked: bool):
        if (self._worker and self._worker.isRunning()) or \
           (self._calib_worker and self._calib_worker.isRunning()):
            self._log("❌ 동작 중 SIM 모드 전환 불가")
            self.btn_sim.setChecked(not checked)  # 토글 되돌리기
            return
        if checked:
            from core.simulator import SimCamera, SimMotorPanel
            self._sim_cam   = SimCamera()
            self._sim_motor = SimMotorPanel(self._sim_cam)
            self.set_shared_camera(self._sim_cam)
            self._motor_panel = self._sim_motor
            self._lbl_cam.setText("🟡 SIM  ● Gaussian Beam  512×512")
            self._lbl_cam.setStyleSheet(
                "color: #ffe66d; font-family: 'Courier New'; font-size: 11px;"
            )
            self.btn_sim.setText("■  SIM OFF")
            self._log("🟡 SIM MODE 활성 — 가상 카메라 + M1/M2/M3 가중치 비대칭 모터")
        else:
            self._sim_cam   = None
            self._sim_motor = None
            self.clear_shared_camera()
            self._motor_panel = None
            self.btn_sim.setText("▷  SIM MODE")
            self._log("⬛ SIM MODE 해제")

    def _browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.edit_save_dir.text())
        if path:
            self.edit_save_dir.setText(path)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        if any(k in msg for k in ("✅", "▶")):
            color = "#4ecdc4"
        elif "⚠️" in msg:
            color = "#ffe66d"
        elif "❌" in msg:
            color = "#e94560"
        elif "■" in msg:
            color = "#4a5a7a"
        else:
            color = "#00cc88"
        self.log_display.append(
            f"<span style='color:#2a4060;font-size:10px'>[{ts}]</span> "
            f"<span style='color:{color}'>{msg}</span>"
        )

    def _restore_settings(self):
        s = QSettings("SpeAnalyze", "ScanTab")
        self.combo_motor.setCurrentText(s.value("motor", "M1"))
        self.spin_steps_move.setValue(int(s.value("steps_move", 500)))
        self.spin_num_steps.setValue(int(s.value("num_steps", 10)))
        self.spin_settle.setValue(int(s.value("settle_ms", 500)))
        self.edit_save_dir.setText(s.value("save_dir", "Scan_Data"))
        self.edit_scan_name.setText(s.value("scan_name", "Scan"))

    def cleanup(self):
        s = QSettings("SpeAnalyze", "ScanTab")
        s.setValue("motor",      self.combo_motor.currentText())
        s.setValue("steps_move", self.spin_steps_move.value())
        s.setValue("num_steps",  self.spin_num_steps.value())
        s.setValue("settle_ms",  self.spin_settle.value())
        s.setValue("save_dir",   self.edit_save_dir.text())
        s.setValue("scan_name",  self.edit_scan_name.text())
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(2000)
        if self._calib_worker and self._calib_worker.isRunning():
            self._calib_worker.request_stop()
            self._calib_worker.wait(2000)
