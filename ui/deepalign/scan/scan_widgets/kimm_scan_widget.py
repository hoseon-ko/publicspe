"""KIMM Z 스캔 워크플로우 위젯 — 패널과 분리된 standalone UI.

scan_requested(points, settle_ms, avg_frames)에서
  points: list[float] (Z µm absolute)
"""

from __future__ import annotations
import numpy as np

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSpinBox, QDoubleSpinBox,
    QPushButton, QLabel, QFrame, QCheckBox,
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

        self.spin_z_start  = _dspin(-100000.0, 100000.0, 0.0)
        self.spin_z_end    = _dspin(-100000.0, 100000.0, 10.0)
        self.spin_n        = _ispin(2, 9999, 5)
        self.spin_settle   = _ispin(0, 10000, 200)
        self.spin_avg      = _ispin(1, 32, 1)
        self.spin_timeout  = _dspin(1.0, 120.0, 30.0, decs=1, step=1.0, suffix=" s")

        grid.addWidget(_lbl("Z start"),       0, 0); grid.addWidget(self.spin_z_start, 0, 1)
        grid.addWidget(_lbl("Z end"),         0, 2); grid.addWidget(self.spin_z_end,   0, 3)
        grid.addWidget(_lbl("N points"),      1, 0); grid.addWidget(self.spin_n,       1, 1)
        grid.addWidget(_lbl("Settle ms"),     1, 2); grid.addWidget(self.spin_settle,  1, 3)
        grid.addWidget(_lbl("Avg frames"),    2, 0); grid.addWidget(self.spin_avg,     2, 1)
        grid.addWidget(_lbl("Move Timeout"),  2, 2); grid.addWidget(self.spin_timeout, 2, 3)
        params_l.addLayout(grid)
        lay.addWidget(sec_params)

        self.chk_save_spe = QCheckBox("💾 Save SPE (스캔 종료 시 단일 multi-frame 파일)")
        self.chk_save_spe.setChecked(False)
        self.chk_save_spe.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
            f" font-size: 11px; background: transparent; border: none;"
        )
        lay.addWidget(self.chk_save_spe)

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

    def _on_start(self) -> None:
        z0 = float(self.spin_z_start.value())
        z1 = float(self.spin_z_end.value())
        n  = int(self.spin_n.value())
        z_positions = list(np.linspace(z0, z1, n))
        self.scan_requested.emit(z_positions, int(self.spin_settle.value()), int(self.spin_avg.value()))

    def set_scan_status(self, msg: str, kind: str = "info") -> None:
        apply_status(self.lbl_status, msg, kind)

    def set_scan_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        if not running:
            self.phase_indicator.reset()

    def set_phase(self, idx: int, total: int, phase: str) -> None:
        self.phase_indicator.set_phase(idx, total, phase)

    def get_move_timeout_ms(self) -> int:
        return int(round(float(self.spin_timeout.value()) * 1000.0))

    def is_save_spe_enabled(self) -> bool:
        return bool(self.chk_save_spe.isChecked())
