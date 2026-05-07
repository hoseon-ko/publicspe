"""
ui/autofocus/autofocus_tab.py
KIMM Z 오토포커스 탭 — Z 스캔 + Contrast 최대 포커스 탐색.

레이아웃:
  ┌─ 좌측 설정 패널 (280px) ─┬─ 우측 뷰 영역 (QSplitter) ─────────┐
  │  📷 CAMERA               │  ImageViewer (스캔 중 프리뷰)        │
  │  🔍 SCAN RANGE           ├─────────────────────────────────────┤
  │  📊 METRIC               │  Sharpness vs Z 플롯                 │
  │  ⚙ OPTIONS              │  + 결과 테이블                        │
  │  ─────────               └─────────────────────────────────────┘
  │  [ ▶ RUN ] [ ■ STOP ]
  │  ████████░  60%
  │  ─────────
  │  Best Z: +12.50 µm [GO]
  └──────────────────────────
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QScrollArea, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QComboBox,
    QProgressBar, QTextEdit, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

import pyqtgraph as pg

from ui.image_viewer import ImageViewer
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    Fonts, Sizes,
    C_ACCENT, C_DANGER, C_BORDER,
    C_BG_DARK, C_BG_MED, C_TEXT_DIM, C_TEXT_DEAD,
    SPIN_STYLE, COMBO_STYLE, TEXTEDIT_LOG,
    BTN_PRIMARY, BTN_DANGER,
)

_FC  = Fonts.MONO
_FS  = Sizes.CTRL
_FSS = Sizes.SMALL
_FSB = Sizes.BTN


# ── 버튼 / 레이블 헬퍼 ────────────────────────────────────────────────
def _lbl(text: str, color: str = C_TEXT_DIM) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-family: '{_FC}'; font-size: {_FSS};"
    )
    return w


def _sep_h() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {C_BORDER}; margin: 3px 0;")
    return f


def _btn(text: str, color: str) -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(f"""
        QPushButton {{
            background: transparent; color: {color};
            border: 1px solid {color}; border-radius: 3px;
            font-family: '{_FC}'; font-size: {_FS};
            font-weight: bold; padding: 5px 12px;
        }}
        QPushButton:hover  {{ background: {color}22; }}
        QPushButton:pressed {{ background: {color}44; }}
        QPushButton:disabled {{ color: #304060; border-color: #1a2840; }}
    """)
    return b


# ─────────────────────────────────────────────────────────────────────
class AutoFocusTab(QWidget):
    """
    독립 오토포커스 탭.

    외부 시그널:
      af_starting()          — RUN 시작 → Live 스트림 정지 요청
      af_done()              — 완료   → Live 스트림 재개 요청
      log_message(str)       — 상태바 전달
    """

    af_starting  = pyqtSignal()
    af_done      = pyqtSignal()
    log_message  = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cam  = None          # Live 탭에서 공유받는 카메라
        self._kimm = None          # KIMMZController (공유)
        self._running = False

        # 결과 데이터
        self._z_pts:  list[float] = []
        self._sh_pts: list[float] = []
        self._best_z: Optional[float] = None

        self._build_ui()

    # ── Public API (MainWindow에서 연결) ──────────────────────────────

    def set_shared_camera(self, cam):
        self._cam = cam
        name = type(cam).__name__.replace("Camera", "")
        self._lbl_cam.setText(f"● {name}  CONNECTED")
        self._lbl_cam.setStyleSheet(
            f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self.btn_run.setEnabled(True)

    def clear_shared_camera(self):
        self._cam = None
        self._lbl_cam.setText("● 카메라 없음")
        self._lbl_cam.setStyleSheet(
            f"color: {C_DANGER}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self.btn_run.setEnabled(False)

    def set_kimm_ctrl(self, ctrl):
        """Live 탭의 KIMMZController 공유."""
        self._kimm = ctrl
        self._update_kimm_status()

    def cleanup(self):
        pass   # Worker 종료 처리 (추후)

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #1a3a60; width: 3px; }"
        )
        root.addWidget(splitter)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([290, 1200])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # ── 좌측 설정 패널 ────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #0a0f1e; }"
            "QScrollBar:vertical { width: 6px; background: #0a1020; }"
            "QScrollBar::handle:vertical { background: #1a3060; border-radius: 3px; }"
        )

        inner = QWidget()
        inner.setStyleSheet("background: #0a0f1e;")
        v = QVBoxLayout(inner)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # ── 1. 카메라 상태 ─────────────────────────────────────────────
        sec_cam = CollapsibleSection("📷  CAMERA", accent=C_ACCENT)
        self._lbl_cam = QLabel("● 카메라 없음")
        self._lbl_cam.setStyleSheet(
            f"color: {C_DANGER}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        sec_cam.add_widget(self._lbl_cam)
        v.addWidget(sec_cam)

        # ── 2. KIMM Z 상태 ─────────────────────────────────────────────
        sec_kimm = CollapsibleSection("🎯  KIMM Z", accent="#9a6a4a")
        self._lbl_kimm = QLabel("● 연결 없음")
        self._lbl_kimm.setStyleSheet(
            f"color: {C_TEXT_DEAD}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._lbl_z_cur = QLabel("Z: —  µm")
        self._lbl_z_cur.setStyleSheet(
            f"color: #8090b0; font-family: '{_FC}'; font-size: {_FSS};"
        )
        # 100ms 폴링 타이머 (KIMM 연결 시 활성화)
        self._z_timer = QTimer()
        self._z_timer.setInterval(150)
        self._z_timer.timeout.connect(self._poll_z)
        sec_kimm.add_widget(self._lbl_kimm)
        sec_kimm.add_widget(self._lbl_z_cur)
        v.addWidget(sec_kimm)

        # ── 3. Z 스캔 범위 ─────────────────────────────────────────────
        sec_range = CollapsibleSection("🔍  Z SCAN RANGE", accent="#4a9a7a")
        rl = sec_range.content_layout()
        rl.setSpacing(5)

        def _row(label: str, widget: QWidget) -> QHBoxLayout:
            h = QHBoxLayout()
            h.setSpacing(6)
            lb = QLabel(label)
            lb.setFixedWidth(70)
            lb.setStyleSheet(
                f"color: {C_TEXT_DIM}; font-family: '{_FC}'; font-size: {_FSS};"
            )
            lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(lb)
            h.addWidget(widget, 1)
            return h

        self.spin_center = QDoubleSpinBox()
        self.spin_center.setRange(-1e6, 1e6)
        self.spin_center.setDecimals(2)
        self.spin_center.setSuffix("  µm")
        self.spin_center.setValue(0.0)
        self.spin_center.setStyleSheet(SPIN_STYLE)

        self.spin_range = QDoubleSpinBox()
        self.spin_range.setRange(0.1, 1e5)
        self.spin_range.setDecimals(2)
        self.spin_range.setSuffix("  µm")
        self.spin_range.setValue(50.0)
        self.spin_range.setStyleSheet(SPIN_STYLE)

        self.spin_step = QDoubleSpinBox()
        self.spin_step.setRange(0.1, 1e4)
        self.spin_step.setDecimals(2)
        self.spin_step.setSuffix("  µm")
        self.spin_step.setValue(5.0)
        self.spin_step.setStyleSheet(SPIN_STYLE)

        rl.addLayout(_row("Center", self.spin_center))
        rl.addLayout(_row("± Range", self.spin_range))
        rl.addLayout(_row("Step", self.spin_step))

        self._lbl_steps = QLabel("Steps: 21")
        self._lbl_steps.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lbl_steps.setStyleSheet(
            f"color: #4a7a6a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        rl.addWidget(self._lbl_steps)

        for s in (self.spin_range, self.spin_step):
            s.valueChanged.connect(self._update_step_count)
        self._update_step_count()
        v.addWidget(sec_range)

        # ── 4. 선명도 지표 ─────────────────────────────────────────────
        sec_metric = CollapsibleSection("📊  SHARPNESS METRIC", accent="#6a6aaa")
        ml = sec_metric.content_layout()
        ml.setSpacing(5)

        self.combo_metric = QComboBox()
        self.combo_metric.addItems([
            "Laplacian Variance",
            "Contrast (Std Dev)",
            "Tenengrad (Sobel²)",
            "Brenner",
        ])
        self.combo_metric.setStyleSheet(COMBO_STYLE)
        ml.addWidget(self.combo_metric)

        ml.addWidget(_lbl("Laplacian: 가장 일반적, 고주파 선명도"))
        ml.addWidget(_lbl("Tenengrad: 노이즈에 강함"))
        v.addWidget(sec_metric)

        # ── 5. 옵션 ────────────────────────────────────────────────────
        sec_opt = CollapsibleSection("⚙  OPTIONS", accent="#7a6a4a", collapsed=True)
        ol = sec_opt.content_layout()
        ol.setSpacing(5)

        self.spin_settle = QSpinBox()
        self.spin_settle.setRange(0, 5000)
        self.spin_settle.setSuffix("  ms")
        self.spin_settle.setValue(200)
        self.spin_settle.setToolTip("이동 후 진동 안정 대기 시간")
        self.spin_settle.setStyleSheet(SPIN_STYLE)
        ol.addLayout(_row("Settle", self.spin_settle))

        self.spin_avg = QSpinBox()
        self.spin_avg.setRange(1, 32)
        self.spin_avg.setValue(1)
        self.spin_avg.setToolTip("각 위치에서 평균낼 프레임 수")
        self.spin_avg.setStyleSheet(SPIN_STYLE)
        ol.addLayout(_row("Avg frames", self.spin_avg))

        self.chk_goto_best = QCheckBox("완료 후 Best Z로 자동 이동")
        self.chk_goto_best.setChecked(True)
        self.chk_goto_best.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{_FC}'; font-size: {_FSS};"
        )
        ol.addWidget(self.chk_goto_best)

        v.addWidget(sec_opt)
        v.addWidget(_sep_h())

        # ── 6. RUN / STOP ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_run  = _btn("▶  RUN",  "#4ecdc4")
        self.btn_stop = _btn("■  STOP", "#e94560")
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_run.clicked.connect(self._on_run)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(self.btn_run,  1)
        btn_row.addWidget(self.btn_stop, 1)
        v.addLayout(btn_row)

        # 진행 상태
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(14)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background: #080e1e; border: 1px solid #1a3060;
                border-radius: 3px; color: #4a6a8a;
                font-family: '{_FC}'; font-size: {_FSS}; text-align: center;
            }}
            QProgressBar::chunk {{ background: {C_ACCENT}; border-radius: 2px; }}
        """)
        v.addWidget(self.progress)

        self._lbl_status = QLabel("Ready")
        self._lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_status.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        v.addWidget(self._lbl_status)
        v.addWidget(_sep_h())

        # ── 7. 결과 ────────────────────────────────────────────────────
        v.addWidget(_lbl("RESULT", "#2a4a6a"))

        res_row = QHBoxLayout()
        v.addLayout(res_row)
        self._lbl_best_z = QLabel("Best Z:  —")
        self._lbl_best_z.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: 14px; font-weight: bold;"
        )
        self.btn_goto = _btn("GO", "#e94560")
        self.btn_goto.setFixedWidth(48)
        self.btn_goto.setEnabled(False)
        self.btn_goto.setToolTip("Best Z 위치로 이동")
        self.btn_goto.clicked.connect(self._on_goto)
        res_row.addWidget(self._lbl_best_z, 1)
        res_row.addWidget(self.btn_goto)

        self._lbl_best_sh = QLabel("Sharpness:  —")
        self._lbl_best_sh.setStyleSheet(
            f"color: #4a9a7a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        v.addWidget(self._lbl_best_sh)
        v.addWidget(_sep_h())

        # ── 8. 로그 ────────────────────────────────────────────────────
        v.addWidget(_lbl("LOG", "#2a4a6a"))
        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(120)
        self._log_box.setStyleSheet(TEXTEDIT_LOG)
        v.addWidget(self._log_box)

        v.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    # ── 우측 뷰 패널 ──────────────────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet("background: #080e1e;")
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setStyleSheet(
            "QSplitter::handle { background: #1a3a60; height: 3px; }"
        )

        # ── 상단: 이미지 뷰어 (각 스텝 프리뷰) ───────────────────────
        viewer_wrap = QWidget()
        viewer_wrap.setStyleSheet("background: #080e1e;")
        vv = QVBoxLayout(viewer_wrap)
        vv.setContentsMargins(0, 0, 0, 0)
        vv.setSpacing(0)

        # 뷰어 헤더
        vhdr = QWidget()
        vhdr.setFixedHeight(22)
        vhdr.setStyleSheet(f"background: {C_BG_MED}; border-bottom: 1px solid {C_BORDER};")
        vhdr_h = QHBoxLayout(vhdr)
        vhdr_h.setContentsMargins(8, 0, 8, 0)
        lbl_view = QLabel("PREVIEW")
        lbl_view.setStyleSheet(
            f"color: #3a5878; font-family: '{_FC}'; font-size: {_FSS};"
            " font-weight: bold; letter-spacing: 2px;"
        )
        self._lbl_step_info = QLabel("—")
        self._lbl_step_info.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FSS};"
        )
        self._lbl_step_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vhdr_h.addWidget(lbl_view)
        vhdr_h.addWidget(self._lbl_step_info, 1)
        vv.addWidget(vhdr)

        self.image_viewer = ImageViewer()
        vv.addWidget(self.image_viewer, 1)
        vsplit.addWidget(viewer_wrap)

        # ── 하단: Sharpness vs Z 플롯 ─────────────────────────────────
        plot_wrap = QWidget()
        plot_wrap.setStyleSheet("background: #080e1e;")
        pv = QVBoxLayout(plot_wrap)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        phdr = QWidget()
        phdr.setFixedHeight(22)
        phdr.setStyleSheet(f"background: {C_BG_MED}; border-bottom: 1px solid {C_BORDER};")
        phdr_h = QHBoxLayout(phdr)
        phdr_h.setContentsMargins(8, 0, 8, 0)
        lbl_plot = QLabel("SHARPNESS vs Z")
        lbl_plot.setStyleSheet(
            f"color: #3a5878; font-family: '{_FC}'; font-size: {_FSS};"
            " font-weight: bold; letter-spacing: 2px;"
        )
        phdr_h.addWidget(lbl_plot)
        pv.addWidget(phdr)
        pv.addWidget(self._build_plot(), 1)
        vsplit.addWidget(plot_wrap)

        vsplit.setSizes([600, 300])
        v.addWidget(vsplit, 1)
        return container

    def _build_plot(self) -> pg.PlotWidget:
        pg.setConfigOptions(antialias=True)
        pw = pg.PlotWidget()
        pw.setBackground("#080e1e")

        for ax in ("bottom", "left"):
            axis = pw.getAxis(ax)
            axis.setPen(pg.mkPen("#2a3a52"))
            axis.setTextPen(pg.mkPen(C_TEXT_DIM))
            axis.setStyle(tickFont=pg.QtGui.QFont(_FC, 8))

        pw.getAxis("bottom").setLabel(
            "Z position (µm)", **{"color": C_TEXT_DIM, "font-size": "9px"}
        )
        pw.getAxis("left").setLabel(
            "Sharpness (a.u.)", **{"color": C_TEXT_DIM, "font-size": "9px"}
        )
        pw.showGrid(x=True, y=True, alpha=0.2)
        pw.getPlotItem().getViewBox().setBackgroundColor("#080e1e")

        # 선명도 곡선
        self._curve = pw.plot(
            pen=pg.mkPen("#4ecdc4", width=2),
            symbol="o", symbolSize=5,
            symbolBrush="#4ecdc4", symbolPen=None,
            name="Sharpness",
        )
        # 최적 Z 수직선 (빨간 점선)
        self._best_vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#e94560", width=1.5,
                         style=Qt.PenStyle.DashLine),
        )
        pw.addItem(self._best_vline)
        self._best_vline.hide()

        self._plot_widget = pw
        return pw

    # ── 슬롯 ─────────────────────────────────────────────────────────

    def _update_step_count(self):
        half = self.spin_range.value()
        step = max(self.spin_step.value(), 0.001)
        n = int(2 * half / step) + 1
        self._lbl_steps.setText(f"Steps: {n}")

    def _update_kimm_status(self):
        if self._kimm and self._kimm.is_connected:
            self._lbl_kimm.setText("● CONNECTED")
            self._lbl_kimm.setStyleSheet(
                f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FSS};"
            )
            self._z_timer.start()
        else:
            self._lbl_kimm.setText("● 연결 없음")
            self._lbl_kimm.setStyleSheet(
                f"color: {C_TEXT_DEAD}; font-family: '{_FC}'; font-size: {_FSS};"
            )
            self._z_timer.stop()

    def _poll_z(self):
        if self._kimm and self._kimm.is_connected:
            self._kimm.request_position()
            z = self._kimm.current_z
            self._lbl_z_cur.setText(f"Z: {z:+.3f}  µm")
        else:
            self._z_timer.stop()

    def _on_run(self):
        center = self.spin_center.value()
        half   = self.spin_range.value()
        step   = self.spin_step.value()
        n      = int(2 * half / max(step, 0.001)) + 1
        metric_map = {
            "Laplacian Variance":  "laplacian",
            "Contrast (Std Dev)":  "contrast",
            "Tenengrad (Sobel²)":  "tenengrad",
            "Brenner":             "brenner",
        }
        metric = metric_map.get(self.combo_metric.currentText(), "laplacian")

        self._log(
            f"AF START — center={center:+.1f}µm  ±{half:.1f}µm  "
            f"step={step:.1f}µm  {n}steps  [{metric}]"
        )

        # 초기화
        self._z_pts.clear()
        self._sh_pts.clear()
        self._best_z = None
        self._curve.setData([], [])
        self._best_vline.hide()
        self._lbl_best_z.setText("Best Z:  —")
        self._lbl_best_sh.setText("Sharpness:  —")
        self.btn_goto.setEnabled(False)
        self.progress.setValue(0)
        self._lbl_status.setText("Running…")
        self._lbl_step_info.setText("—")

        self._set_running(True)
        self.af_starting.emit()

        # TODO: AutoFocusWorker 연결
        # self._worker = AutoFocusWorker(self._cam, self._kimm,
        #     center, half, step, metric,
        #     settle_ms=self.spin_settle.value(),
        #     avg_frames=self.spin_avg.value())
        # self._worker.step_done.connect(self._on_step_done)
        # self._worker.finished.connect(self._on_af_finished)
        # self._worker.error.connect(self._on_af_error)
        # self._worker.start()

        # Worker 미구현 → 즉시 idle 복귀
        self._lbl_status.setText("Worker 미연결 — 구현 후 동작")
        self._set_running(False)

    def _on_stop(self):
        self._log("AF STOP 요청")
        # TODO: self._worker.request_stop()
        self._lbl_status.setText("중단됨")
        self._set_running(False)

    def _on_goto(self):
        if self._best_z is None:
            return
        if self._kimm is None or not self._kimm.is_connected:
            self._log("KIMM 미연결 — 이동 불가")
            return
        self._log(f"Best Z로 이동: {self._best_z:+.2f} µm")
        # TODO: self._kimm.move_to_z(self._best_z)

    # ── Worker 콜백 (Worker 구현 후 연결) ────────────────────────────

    def on_step_done(self, step: int, total: int,
                     z: float, sharpness: float,
                     frame: np.ndarray):
        """각 스텝 완료 시 Worker에서 호출."""
        pct = int(step / max(total, 1) * 100)
        self.progress.setValue(pct)
        self._lbl_status.setText(
            f"Step {step}/{total}  Z={z:+.2f}µm  S={sharpness:.1f}"
        )
        self._lbl_step_info.setText(
            f"Step {step}/{total}  •  Z = {z:+.2f} µm"
        )

        self._z_pts.append(z)
        self._sh_pts.append(sharpness)
        self._curve.setData(self._z_pts, self._sh_pts)

        self.image_viewer.set_image(frame)

    def on_af_finished(self, best_z: float, best_sh: float):
        """스캔 완료 시 Worker에서 호출."""
        self._best_z = best_z
        self._lbl_best_z.setText(f"Best Z:  {best_z:+.2f}  µm")
        self._lbl_best_sh.setText(f"Sharpness:  {best_sh:.1f}")
        self._best_vline.setPos(best_z)
        self._best_vline.show()
        self.btn_goto.setEnabled(True)
        self.progress.setValue(100)
        self._lbl_status.setText(f"완료 — Best Z: {best_z:+.2f} µm")
        self._log(f"AF 완료 — Best Z: {best_z:+.2f} µm  Sharpness: {best_sh:.1f}")
        self._set_running(False)
        self.af_done.emit()

        if self.chk_goto_best.isChecked():
            self._on_goto()

    def on_af_error(self, msg: str):
        self._log(f"AF ERROR: {msg}")
        self._lbl_status.setText(f"오류: {msg}")
        self._set_running(False)
        self.af_done.emit()

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        self.btn_run.setEnabled(not running and self._cam is not None)
        self.btn_stop.setEnabled(running)
        for w in (self.spin_center, self.spin_range,
                  self.spin_step, self.combo_metric,
                  self.spin_settle, self.spin_avg):
            w.setEnabled(not running)

    def _log(self, msg: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.append(
            f'<span style="color:#4a6a8a">[{ts}]</span>'
            f' <span style="color:#a0c0e0">{msg}</span>'
        )
        self.log_message.emit(msg)
