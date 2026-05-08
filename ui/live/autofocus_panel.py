"""
ui/live/autofocus_panel.py
KIMM Z 오토포커스 패널 — Contrast-based Z-scan UI.

기능:
  - Z 범위 (Center ± Range 또는 Start/End) + Step 설정
  - 선명도 지표 선택 (Laplacian / Contrast / Tenengrad)
  - Sharpness vs Z 실시간 플롯
  - 진행 상태 + 최적 Z 결과 표시
  - RUN / STOP 버튼
  (실제 이동·촬영 로직은 별도 Worker에서 — 현재는 UI only)
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
    QComboBox, QProgressBar, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal

import pyqtgraph as pg

from theme.styles import Fonts, Sizes, C_ACCENT, C_BORDER, C_BG_DARK, C_BG_MED, C_TEXT_DIM

_FC  = Fonts.MONO
_FS  = Sizes.CTRL
_FSS = Sizes.SMALL

# ── 색상 토큰 ──────────────────────────────────────────────────────────
_C_PLOT_LINE  = "#4ecdc4"   # 선명도 곡선
_C_BEST_LINE  = "#e94560"   # 최적 Z 수직선
_C_AXIS       = "#2a3a52"   # 축 색
_C_GRID       = "#1a2a3e"   # 그리드
_C_INPUT_BG   = "#080e1e"
_C_INPUT_BD   = "#1a3060"

# ── 공통 QSS ───────────────────────────────────────────────────────────
_SPIN_QSS = f"""
    QDoubleSpinBox, QSpinBox {{
        background: {_C_INPUT_BG}; border: 1px solid {_C_INPUT_BD};
        color: #c0d0ff; border-radius: 3px;
        font-family: '{_FC}'; font-size: {_FS}; padding: 1px 4px;
    }}
    QDoubleSpinBox:focus, QSpinBox:focus {{
        border-color: {C_ACCENT};
    }}
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
    QSpinBox::up-button,       QSpinBox::down-button {{
        width: 14px; border: none;
        background: #0d1e38;
    }}
"""
_COMBO_QSS = f"""
    QComboBox {{
        background: {_C_INPUT_BG}; border: 1px solid {_C_INPUT_BD};
        color: #c0d0ff; border-radius: 3px;
        font-family: '{_FC}'; font-size: {_FS}; padding: 1px 6px;
    }}
    QComboBox:focus {{ border-color: {C_ACCENT}; }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox QAbstractItemView {{
        background: #0d1e38; color: #c0d0ff;
        border: 1px solid {_C_INPUT_BD};
        selection-background-color: #1a3a60;
    }}
"""
_LBL_QSS = f"color: {C_TEXT_DIM}; font-family: '{_FC}'; font-size: {_FSS};"

def _btn_qss(color: str) -> str:
    return f"""
        QPushButton {{
            background: transparent; color: {color};
            border: 1px solid {color}; border-radius: 3px;
            font-family: '{_FC}'; font-size: {_FS};
            font-weight: bold; padding: 4px 10px;
        }}
        QPushButton:hover {{ background: {color}22; }}
        QPushButton:disabled {{ color: #304060; border-color: #1a2840; }}
    """

def _sep_h() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {C_BORDER}; margin: 2px 0;")
    return f


class AutoFocusPanel(QWidget):
    """
    KIMM Z 오토포커스 설정 + 결과 시각화 패널.

    시그널:
      run_requested(center, half_range, step, metric)  — RUN 버튼 클릭 시
      stop_requested()                                  — STOP 버튼 클릭 시
    """

    run_requested  = pyqtSignal(float, float, float, str)  # center, half_range, step, metric
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._running   = False
        self._z_data:  list[float] = []
        self._sh_data: list[float] = []
        self._best_z:  float | None = None
        self._build_ui()

    # ── UI 구성 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        # ── 1. 범위 설정 ──────────────────────────────────────────────
        root.addWidget(self._section_label("Z SCAN RANGE"))

        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setColumnStretch(1, 1)

        def _lbl(txt):
            l = QLabel(txt)
            l.setStyleSheet(_LBL_QSS)
            l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return l

        # Center
        grid.addWidget(_lbl("Center"), 0, 0)
        self.spin_center = QDoubleSpinBox()
        self.spin_center.setRange(-1e6, 1e6)
        self.spin_center.setDecimals(2)
        self.spin_center.setSuffix("  µm")
        self.spin_center.setValue(0.0)
        self.spin_center.setStyleSheet(_SPIN_QSS)
        grid.addWidget(self.spin_center, 0, 1)

        # Range (±)
        grid.addWidget(_lbl("± Range"), 1, 0)
        self.spin_range = QDoubleSpinBox()
        self.spin_range.setRange(0.1, 1e5)
        self.spin_range.setDecimals(2)
        self.spin_range.setSuffix("  µm")
        self.spin_range.setValue(50.0)
        self.spin_range.setStyleSheet(_SPIN_QSS)
        grid.addWidget(self.spin_range, 1, 1)

        # Step
        grid.addWidget(_lbl("Step"), 2, 0)
        self.spin_step = QDoubleSpinBox()
        self.spin_step.setRange(0.1, 1e4)
        self.spin_step.setDecimals(2)
        self.spin_step.setSuffix("  µm")
        self.spin_step.setValue(5.0)
        self.spin_step.setStyleSheet(_SPIN_QSS)
        self.spin_step.valueChanged.connect(self._update_step_count)
        self.spin_range.valueChanged.connect(self._update_step_count)
        grid.addWidget(self.spin_step, 2, 1)

        root.addLayout(grid)

        # 스텝 수 표시
        self.lbl_steps = QLabel("Steps: 21")
        self.lbl_steps.setStyleSheet(
            f"color: #4a7a6a; font-family: '{_FC}'; font-size: {_FSS};"
            " text-align: right;"
        )
        self.lbl_steps.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.lbl_steps)
        self._update_step_count()

        root.addWidget(_sep_h())

        # ── 2. 선명도 지표 ────────────────────────────────────────────
        root.addWidget(self._section_label("SHARPNESS METRIC"))

        self.combo_metric = QComboBox()
        self.combo_metric.addItems([
            "Laplacian Variance",
            "Contrast (Std Dev)",
            "Tenengrad (Sobel²)",
            "Brenner",
        ])
        self.combo_metric.setStyleSheet(_COMBO_QSS)
        root.addWidget(self.combo_metric)

        root.addWidget(_sep_h())

        # ── 3. RUN / STOP ─────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("▶  RUN")
        self.btn_run.setStyleSheet(_btn_qss("#4ecdc4"))
        self.btn_run.clicked.connect(self._on_run)

        self.btn_stop = QPushButton("■  STOP")
        self.btn_stop.setStyleSheet(_btn_qss("#e94560"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)

        btn_row.addWidget(self.btn_run, 1)
        btn_row.addWidget(self.btn_stop, 1)
        root.addLayout(btn_row)

        # ── 4. 진행 상태 ──────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(14)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background: {_C_INPUT_BG}; border: 1px solid {_C_INPUT_BD};
                border-radius: 3px; color: #4a6a8a;
                font-family: '{_FC}'; font-size: {_FSS};
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: {C_ACCENT}; border-radius: 2px;
            }}
        """)
        root.addWidget(self.progress)

        # 현재 Z 위치 표시 (스캔 중)
        self.lbl_current = QLabel("Z: —")
        self.lbl_current.setStyleSheet(
            f"color: #4a6a8a; font-family: '{_FC}'; font-size: {_FSS};"
            " text-align: center;"
        )
        self.lbl_current.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_current)

        root.addWidget(_sep_h())

        # ── 5. 결과: 최적 Z ───────────────────────────────────────────
        root.addWidget(self._section_label("RESULT"))

        result_row = QHBoxLayout()
        lbl_best = QLabel("Best Z")
        lbl_best.setStyleSheet(_LBL_QSS)

        self.lbl_best_z = QLabel("—  µm")
        self.lbl_best_z.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: 18px;"
            " font-weight: bold;"
        )
        self.lbl_best_z.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_goto = QPushButton("GO")
        self.btn_goto.setFixedWidth(42)
        self.btn_goto.setToolTip("Best Z 위치로 이동")
        self.btn_goto.setStyleSheet(_btn_qss("#e94560"))
        self.btn_goto.setEnabled(False)

        result_row.addWidget(lbl_best)
        result_row.addWidget(self.lbl_best_z, 1)
        result_row.addWidget(self.btn_goto)
        root.addLayout(result_row)

        root.addWidget(_sep_h())

        # ── 6. Sharpness vs Z 플롯 ────────────────────────────────────
        root.addWidget(self._section_label("SHARPNESS CURVE"))
        root.addWidget(self._build_plot())

        root.addStretch(1)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: #2a4a6a; font-family: '{_FC}'; font-size: {_FSS};"
            " font-weight: bold; letter-spacing: 2px;"
        )
        return lbl

    def _build_plot(self) -> pg.PlotWidget:
        pg.setConfigOptions(antialias=True)
        self._plot = pg.PlotWidget()
        self._plot.setBackground(_C_INPUT_BG)
        self._plot.setFixedHeight(160)
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # 축 스타일
        for ax in ("bottom", "left"):
            axis = self._plot.getAxis(ax)
            axis.setPen(pg.mkPen(_C_AXIS))
            axis.setTextPen(pg.mkPen(C_TEXT_DIM))
            axis.setStyle(tickFont=pg.QtGui.QFont(_FC, 8))

        self._plot.getAxis("bottom").setLabel("Z position (µm)",
            **{"color": C_TEXT_DIM, "font-size": "9px"})
        self._plot.getAxis("left").setLabel("Sharpness",
            **{"color": C_TEXT_DIM, "font-size": "9px"})

        self._plot.showGrid(x=True, y=True,
                            alpha=0.25)
        self._plot.getPlotItem().getViewBox().setBackgroundColor(_C_INPUT_BG)

        # 커브 + 최적 Z 수직선
        self._curve = self._plot.plot(
            pen=pg.mkPen(_C_PLOT_LINE, width=1.5),
            symbol="o", symbolSize=4,
            symbolBrush=_C_PLOT_LINE, symbolPen=None,
        )
        self._best_line = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(_C_BEST_LINE, width=1, style=Qt.PenStyle.DashLine),
        )
        self._plot.addItem(self._best_line)
        self._best_line.hide()

        return self._plot

    # ── 슬롯 ─────────────────────────────────────────────────────────

    def _update_step_count(self):
        half = self.spin_range.value()
        step = self.spin_step.value()
        if step <= 0:
            return
        n = int(2 * half / step) + 1
        self.lbl_steps.setText(f"Steps: {n}")

    def _on_run(self):
        center    = self.spin_center.value()
        half      = self.spin_range.value()
        step      = self.spin_step.value()
        metric_map = {
            "Laplacian Variance":  "laplacian",
            "Contrast (Std Dev)":  "contrast",
            "Tenengrad (Sobel²)":  "tenengrad",
            "Brenner":             "brenner",
        }
        metric = metric_map.get(self.combo_metric.currentText(), "laplacian")

        self._running = True
        self._z_data.clear()
        self._sh_data.clear()
        self._best_z = None
        self._curve.setData([], [])
        self._best_line.hide()
        self.lbl_best_z.setText("—  µm")
        self.btn_goto.setEnabled(False)
        self.progress.setValue(0)
        self.lbl_current.setText("Z: —")

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._set_inputs_enabled(False)

        self.run_requested.emit(center, half, step, metric)

    def _on_stop(self):
        self.stop_requested.emit()
        self._finish_state()

    def _finish_state(self):
        self._running = False
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_inputs_enabled(True)

    def _set_inputs_enabled(self, en: bool):
        for w in (self.spin_center, self.spin_range,
                  self.spin_step, self.combo_metric):
            w.setEnabled(en)

    # ── 외부에서 호출하는 업데이트 API ────────────────────────────────

    def update_progress(self, step: int, total: int, z: float, sharpness: float):
        """각 스텝 완료 시 Live탭/Worker에서 호출."""
        pct = int(step / max(total, 1) * 100)
        self.progress.setValue(pct)
        self.lbl_current.setText(f"Z: {z:+.2f} µm  |  S: {sharpness:.2f}")

        self._z_data.append(z)
        self._sh_data.append(sharpness)
        self._curve.setData(self._z_data, self._sh_data)

    def set_result(self, best_z: float):
        """스캔 완료 후 Worker에서 호출 — 최적 Z 표시."""
        self._best_z = best_z
        self.lbl_best_z.setText(f"{best_z:+.2f}  µm")
        self._best_line.setPos(best_z)
        self._best_line.show()
        self.btn_goto.setEnabled(True)
        self.progress.setValue(100)
        self.lbl_current.setText(f"Done — Best Z: {best_z:+.2f} µm")
        self._finish_state()

    def set_error(self, msg: str):
        """오류 발생 시 Worker에서 호출."""
        self.lbl_current.setText(f"Error: {msg}")
        self._finish_state()

    @property
    def best_z(self) -> float | None:
        return self._best_z

    @property
    def is_running(self) -> bool:
        return self._running
