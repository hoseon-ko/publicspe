"""KIMM Z 스캔 워크플로우 위젯 — 패널과 분리된 standalone UI.

scan_requested(points, settle_ms, avg_frames)에서
  points: list[float] (Z µm absolute)
"""

from __future__ import annotations
import numpy as np

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSpinBox, QDoubleSpinBox,
    QPushButton,
)

from theme.styles import C_ACCENT, C_DANGER
from ui.deepalign.scan.scan_widgets._common import (
    SPIN_QSS, btn_qss, section_frame, label_dim, status_label, apply_status,
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

        frame, lay = section_frame("KIMM Z SCAN (Generic)", C_ACCENT)
        root.addWidget(frame)

        grid = QGridLayout()
        grid.setSpacing(4)

        def _dspin(lo, hi, val, decs=2, step=1.0, suffix=" µm") -> QDoubleSpinBox:
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setValue(val); s.setDecimals(decs)
            s.setSingleStep(step); s.setSuffix(suffix); s.setStyleSheet(SPIN_QSS)
            return s

        def _ispin(lo, hi, val) -> QSpinBox:
            s = QSpinBox(); s.setRange(lo, hi); s.setValue(val); s.setStyleSheet(SPIN_QSS)
            return s

        self.spin_z_start = _dspin(-100000.0, 100000.0, 0.0)
        self.spin_z_end   = _dspin(-100000.0, 100000.0, 10.0)
        self.spin_n       = _ispin(2, 9999, 5)
        self.spin_settle  = _ispin(0, 10000, 200)
        self.spin_avg     = _ispin(1, 32, 1)

        grid.addWidget(label_dim("Z start"),   0, 0); grid.addWidget(self.spin_z_start, 0, 1)
        grid.addWidget(label_dim("Z end"),     0, 2); grid.addWidget(self.spin_z_end,   0, 3)
        grid.addWidget(label_dim("N points"),  1, 0); grid.addWidget(self.spin_n,       1, 1)
        grid.addWidget(label_dim("Settle ms"), 1, 2); grid.addWidget(self.spin_settle,  1, 3)
        grid.addWidget(label_dim("Avg frames"),2, 0); grid.addWidget(self.spin_avg,     2, 1)
        lay.addLayout(grid)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("SCAN START")
        self.btn_stop  = QPushButton("SCAN STOP")
        self.btn_start.setStyleSheet(btn_qss(C_ACCENT))
        self.btn_stop.setStyleSheet(btn_qss(C_DANGER))
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self.scan_stop_requested)
        btn_row.addWidget(self.btn_start, 1)
        btn_row.addWidget(self.btn_stop, 1)
        lay.addLayout(btn_row)

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
