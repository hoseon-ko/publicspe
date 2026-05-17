"""Mirror(Picomotor) 스캔 워크플로우 위젯 — 패널과 분리된 standalone UI.

scan_requested(points, settle_ms, avg_frames)에서
  points: list[(motor_1based, target_steps_abs)]
"""

from __future__ import annotations
from typing import Callable, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSpinBox, QPushButton, QLabel,
)

from theme.styles import C_ACCENT, C_DANGER
from ui.deepalign.scan.scan_widgets._common import (
    SPIN_QSS, btn_qss, section_frame, label_dim, status_label, apply_status,
)


class MirrorScanWidget(QWidget):
    """Picomotor 스캔 입력 + 시작/정지.

    파라미터: motor / N points / Δ steps / settle_ms / avg_frames
    포인트 생성: (motor, base_pos + i * delta) 형태.

    main_tab은 current_pos_provider를 주입해 baseline pos를 받는다.
    (mover/ctrl에 직접 접근하지 않기 위함)
    """

    scan_requested      = pyqtSignal(list, int, int)
    scan_stop_requested = pyqtSignal()

    def __init__(self, current_pos_provider: Optional[Callable[[int], Optional[int]]] = None,
                 parent=None):
        super().__init__(parent)
        # ctrl.get_position(motor) → int | None. None이면 위젯이 사용자에게 알림
        self._current_pos_provider = current_pos_provider
        self._build_ui()

    def set_current_pos_provider(self, fn: Callable[[int], Optional[int]]) -> None:
        self._current_pos_provider = fn

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        frame, lay = section_frame("MIRROR SCAN (Picomotor)", C_ACCENT)
        root.addWidget(frame)

        grid = QGridLayout()
        grid.setSpacing(4)

        def _spin(lo, hi, val) -> QSpinBox:
            s = QSpinBox(); s.setRange(lo, hi); s.setValue(val); s.setStyleSheet(SPIN_QSS)
            return s

        self.spin_motor  = _spin(1, 4, 1)
        self.spin_n      = _spin(2, 999, 5)
        self.spin_delta  = _spin(-100000, 100000, 100)
        self.spin_settle = _spin(0, 10000, 200)
        self.spin_avg    = _spin(1, 32, 1)

        grid.addWidget(label_dim("Motor"),     0, 0); grid.addWidget(self.spin_motor,  0, 1)
        grid.addWidget(label_dim("N points"),  0, 2); grid.addWidget(self.spin_n,      0, 3)
        grid.addWidget(label_dim("Δ steps"),   1, 0); grid.addWidget(self.spin_delta,  1, 1)
        grid.addWidget(label_dim("Settle ms"), 1, 2); grid.addWidget(self.spin_settle, 1, 3)
        grid.addWidget(label_dim("Avg frames"),2, 0); grid.addWidget(self.spin_avg,    2, 1)
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

    # ── 동작 ──────────────────────────────────────────────────────────────
    def _on_start(self) -> None:
        motor = int(self.spin_motor.value())
        n     = int(self.spin_n.value())
        delta = int(self.spin_delta.value())

        if self._current_pos_provider is None:
            apply_status(self.lbl_status, "현재 위치 provider 미주입", "err"); return
        cur = self._current_pos_provider(motor)
        if cur is None:
            apply_status(self.lbl_status, "위치 조회 실패 (Picomotor 미연결?)", "err"); return

        points = [(motor, int(cur) + delta * i) for i in range(n)]
        self.scan_requested.emit(points, int(self.spin_settle.value()), int(self.spin_avg.value()))

    # ── 외부 호출용 ───────────────────────────────────────────────────────
    def set_scan_status(self, msg: str, kind: str = "info") -> None:
        apply_status(self.lbl_status, msg, kind)

    def set_scan_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
