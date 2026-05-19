"""KIMM Z 스캔 워크플로우 위젯 — 패널과 분리된 standalone UI.

scan_requested(points, settle_ms, avg_frames)에서
  points: list[float] (Z µm absolute)
"""

from __future__ import annotations
import numpy as np

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSpinBox, QDoubleSpinBox,
    QPushButton, QLabel, QFrame, QCheckBox, QComboBox,
)

from theme.styles import (
    C_ACCENT, C_DANGER, C_TEXT_DIM, Fonts, BTN_SMALL, SPIN_STYLE, lbl,
)
from ui.widgets.collapsible_section import CollapsibleSection
from ui.deepalign.scan.scan_widgets._common import (
    status_label, apply_status, PhaseIndicator,
)


class KimmScanWidget(QWidget):
    """KIMM Z 절대 좌표 스캔. start/end 선형 등분."""

    scan_requested      = pyqtSignal(list, int, int)
    scan_stop_requested = pyqtSignal()
    save_last_requested = pyqtSignal()
    servo_on_requested  = pyqtSignal()
    servo_off_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # PicoCard와 동일한 외곽 카드 스타일
        card = QFrame()
        card.setObjectName("motionCard")
        card.setStyleSheet("""
            QFrame#motionCard {
                background: #0f1729;
                border: 1px solid #11345f;
                border-radius: 6px;
            }
        """)
        root.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        # Title — PicoCard 대제목과 동일 톤
        title = QLabel("▾  KIMM Z SCAN")
        title.setStyleSheet(
            f"color: {C_ACCENT}; font-family: '{Fonts.MONO}';"
            f" font-size: 20px; font-weight: bold; letter-spacing: 2px;"
            f" background: transparent; border: none;"
        )
        lay.addWidget(title)

        # Parameters section
        sec_params = CollapsibleSection("SCAN PARAMETERS", accent=C_ACCENT)
        params_l = sec_params.content_layout()

        grid = QGridLayout()
        grid.setSpacing(4)

        def _dspin(lo, hi, val, decs=2, step=1.0, suffix=" µm") -> QDoubleSpinBox:
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setValue(val); s.setDecimals(decs)
            s.setSingleStep(step); s.setSuffix(suffix); s.setStyleSheet(SPIN_STYLE)
            return s

        def _ispin(lo, hi, val) -> QSpinBox:
            s = QSpinBox(); s.setRange(lo, hi); s.setValue(val); s.setStyleSheet(SPIN_STYLE)
            return s

        def _lbl(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(lbl(C_TEXT_DIM, mono=True) + " background: transparent; border: none;")
            return l

        # Center / ±Range / Step 폼 (AutoFocusPanel 와 동일한 직관적 입력)
        # — 사용자가 의도하는 표현 그대로. 워커에는 linspace(center-range, center+range, N) 으로 변환.
        self.spin_center   = _dspin(-1e6, 1e6, 0.0)
        self.spin_range    = _dspin(0.1, 1e5, 50.0)
        self.spin_step     = _dspin(0.1, 1e4, 5.0)
        self.spin_settle   = _ispin(0, 10000, 200)
        self.spin_avg      = _ispin(1, 32, 1)
        self.spin_timeout  = _dspin(1.0, 120.0, 30.0, decs=1, step=1.0, suffix=" s")

        grid.addWidget(_lbl("Center"),        0, 0); grid.addWidget(self.spin_center,  0, 1)
        grid.addWidget(_lbl("± Range"),       0, 2); grid.addWidget(self.spin_range,   0, 3)
        grid.addWidget(_lbl("Step"),          1, 0); grid.addWidget(self.spin_step,    1, 1)
        grid.addWidget(_lbl("Settle ms"),     1, 2); grid.addWidget(self.spin_settle,  1, 3)
        grid.addWidget(_lbl("Avg frames"),    2, 0); grid.addWidget(self.spin_avg,     2, 1)
        grid.addWidget(_lbl("Move Timeout"),  2, 2); grid.addWidget(self.spin_timeout, 2, 3)
        params_l.addLayout(grid)

        # Steps:N 동적 표시
        self.lbl_steps_count = QLabel("Steps: 21")
        self.lbl_steps_count.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lbl_steps_count.setStyleSheet(
            f"color: #4a7a6a; font-family: '{Fonts.MONO}'; font-size: 11px;"
            f" font-weight: bold; background: transparent; border: none;"
            f" padding: 2px 4px;"
        )
        params_l.addWidget(self.lbl_steps_count)
        for sp in (self.spin_range, self.spin_step):
            sp.valueChanged.connect(self._update_steps_count)
        self._update_steps_count()

        # Sharpness Metric (AutoFocus 와 동일)
        metric_row = QHBoxLayout(); metric_row.setSpacing(6)
        metric_row.addWidget(_lbl("Sharpness"))
        self.cb_metric = QComboBox()
        self.cb_metric.addItems([
            "Laplacian Variance",
            "Contrast (Std Dev)",
            "Tenengrad (Sobel²)",
            "Brenner",
        ])
        self.cb_metric.setStyleSheet(
            f"QComboBox {{ background:#080e1e; color:#c0d0ff; border:1px solid #0f3460;"
            f" border-radius:3px; font-family:'{Fonts.MONO}'; font-size:11px; padding:2px 6px; }}"
            f"QComboBox::drop-down {{ border:none; }}"
            f"QComboBox QAbstractItemView {{ background:#0f1729; color:#c0d0ff; }}"
        )
        metric_row.addWidget(self.cb_metric, 1)
        params_l.addLayout(metric_row)

        lay.addWidget(sec_params)

        spe_row = QHBoxLayout(); spe_row.setSpacing(6)
        self.cb_spe_mode = QComboBox()
        self.cb_spe_mode.addItems(["💾 SPE: Off", "💾 SPE: Auto-save", "💾 SPE: Manual"])
        self.cb_spe_mode.setCurrentIndex(0)
        self.cb_spe_mode.setStyleSheet(
            f"QComboBox {{ background:#080e1e; color:#c0d0ff; border:1px solid #0f3460;"
            f" border-radius:3px; font-family:'{Fonts.MONO}'; font-size:11px; padding:2px 6px; }}"
            f"QComboBox::drop-down {{ border:none; }}"
            f"QComboBox QAbstractItemView {{ background:#0f1729; color:#c0d0ff; }}"
        )
        self.btn_save_last = QPushButton("💾 Save Last")
        self.btn_save_last.setEnabled(False)
        self.btn_save_last.setStyleSheet(BTN_SMALL)
        self.btn_save_last.clicked.connect(self.save_last_requested)
        spe_row.addWidget(self.cb_spe_mode, 1)
        spe_row.addWidget(self.btn_save_last)
        lay.addLayout(spe_row)

        # Servo ON/OFF buttons
        servo_row = QHBoxLayout()
        self.btn_servo_on = QPushButton("SERVO ON")
        self.btn_servo_off = QPushButton("SERVO OFF")
        self.btn_servo_on.setStyleSheet(BTN_SMALL)
        self.btn_servo_off.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_servo_on.clicked.connect(self.servo_on_requested.emit)
        self.btn_servo_off.clicked.connect(self.servo_off_requested.emit)
        servo_row.addWidget(self.btn_servo_on)
        servo_row.addWidget(self.btn_servo_off)
        lay.addLayout(servo_row)

        # Action buttons — PicoCard와 동일한 BTN_SMALL
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("SCAN START")
        self.btn_stop  = QPushButton("SCAN STOP")
        self.btn_start.setStyleSheet(BTN_SMALL)
        self.btn_stop.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self.scan_stop_requested)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        lay.addLayout(btn_row)

        self.phase_indicator = PhaseIndicator(accent=C_ACCENT)
        lay.addWidget(self.phase_indicator)

        self.lbl_status = status_label()
        lay.addWidget(self.lbl_status)

    def _compute_steps(self) -> int:
        """range / step 으로 step 수 산출 — 양 끝 포함이라 2*range/step + 1."""
        r = float(self.spin_range.value())
        s = float(self.spin_step.value())
        if s <= 0:
            return 0
        return int(round(2 * r / s)) + 1

    def _update_steps_count(self) -> None:
        n = self._compute_steps()
        self.lbl_steps_count.setText(f"Steps: {n}")

    def _on_start(self) -> None:
        c = float(self.spin_center.value())
        r = float(self.spin_range.value())
        s = float(self.spin_step.value())
        if s <= 0:
            apply_status(self.lbl_status, "Step 은 0보다 커야 함", "err")
            return
        n = max(2, self._compute_steps())
        z0 = c - r
        z1 = c + r
        z_positions = list(np.linspace(z0, z1, n))
        self.scan_requested.emit(z_positions, int(self.spin_settle.value()), int(self.spin_avg.value()))

    def set_scan_status(self, msg: str, kind: str = "info") -> None:
        apply_status(self.lbl_status, msg, kind)

    def set_scan_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_servo_on.setEnabled(not running)
        self.btn_servo_off.setEnabled(not running)
        if not running:
            self.phase_indicator.reset()

    def set_phase(self, idx: int, total: int, phase: str) -> None:
        self.phase_indicator.set_phase(idx, total, phase)

    def get_move_timeout_ms(self) -> int:
        return int(round(float(self.spin_timeout.value()) * 1000.0))

    def get_spe_save_mode(self) -> str:
        return ("off", "auto", "manual")[max(0, self.cb_spe_mode.currentIndex())]

    def is_save_spe_enabled(self) -> bool:
        return self.get_spe_save_mode() != "off"

    def set_save_last_enabled(self, enabled: bool) -> None:
        self.btn_save_last.setEnabled(bool(enabled))
