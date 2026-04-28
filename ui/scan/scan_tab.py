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
)

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
        self._worker: Optional[_ScanWorker] = None
        self._scan_records: list = []

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

    def clear_shared_camera(self):
        self._cam = None
        self._lbl_cam.setText("📷 카메라 없음")
        self._lbl_cam.setStyleSheet("color: #e94560; font-family: 'Courier New'; font-size: 11px;")
        self.btn_start.setEnabled(False)

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
        self._lbl_cam = QLabel("📷 카메라 없음")
        self._lbl_cam.setStyleSheet("color: #e94560; font-family: 'Courier New'; font-size: 11px;")
        gc.addWidget(self._lbl_cam)
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

    # ── 스캔 제어 ─────────────────────────────────────────────────────

    def _start_scan(self):
        if self._cam is None:
            self._log("❌ 카메라 연결 필요")
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

        # UI 상태
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(num_steps)
        self._table.setRowCount(0)
        self._scan_records.clear()
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
        self.btn_stop.setEnabled(False)
        self._log("■ 정지 요청...")

    # ── 워커 콜백 ─────────────────────────────────────────────────────

    def _on_step_done(self, idx: int, result, positions: list, spe_path: str):
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
        self.btn_start.setEnabled(self._cam is not None)
        self.btn_stop.setEnabled(False)
        if csv_path:
            self._log(f"✅ 스캔 완료 — CSV: {csv_path}")
        else:
            self._log("✅ 스캔 완료 (데이터 없음)")
        self._worker = None

    def _on_scan_error(self, msg: str):
        self._log(f"❌ {msg}")
        self.btn_start.setEnabled(self._cam is not None)
        self.btn_stop.setEnabled(False)
        self._worker = None

    # ── 유틸 ─────────────────────────────────────────────────────────

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
