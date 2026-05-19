"""Mirror(Picomotor) 스캔 워크플로우 위젯 — 패널과 분리된 standalone UI.

scan_requested(points, settle_ms, avg_frames)에서
  points: list[(motor_1based, target_steps_abs)]
"""

from __future__ import annotations
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
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
        title = QLabel("▾  MIRROR SCAN")
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

        def _spin(lo, hi, val) -> QSpinBox:
            s = QSpinBox(); s.setRange(lo, hi); s.setValue(val)
            s.setStyleSheet(SPIN_STYLE)
            return s

        def _lbl(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            l.setStyleSheet(l.styleSheet() + " background: transparent; border: none;")
            return l

        self.spin_motor   = _spin(1, 4, 1)
        self.spin_n       = _spin(2, 999, 5)
        self.spin_delta   = _spin(-100000, 100000, 100)
        self.spin_settle  = _spin(0, 10000, 200)
        self.spin_avg     = _spin(1, 32, 1)
        self.spin_timeout = QDoubleSpinBox()
        self.spin_timeout.setRange(1.0, 120.0); self.spin_timeout.setDecimals(1)
        self.spin_timeout.setSingleStep(1.0); self.spin_timeout.setValue(10.0)
        self.spin_timeout.setSuffix(" s"); self.spin_timeout.setStyleSheet(SPIN_STYLE)

        grid.addWidget(_lbl("Motor"),         0, 0); grid.addWidget(self.spin_motor,   0, 1)
        grid.addWidget(_lbl("N points"),      0, 2); grid.addWidget(self.spin_n,       0, 3)
        grid.addWidget(_lbl("Δ steps"),       1, 0); grid.addWidget(self.spin_delta,   1, 1)
        grid.addWidget(_lbl("Settle ms"),     1, 2); grid.addWidget(self.spin_settle,  1, 3)
        grid.addWidget(_lbl("Avg frames"),    2, 0); grid.addWidget(self.spin_avg,     2, 1)
        grid.addWidget(_lbl("Move Timeout"),  2, 2); grid.addWidget(self.spin_timeout, 2, 3)
        params_l.addLayout(grid)
        lay.addWidget(sec_params)

        # Save SPE option
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

        # Phase indicator (MOVE → SETTLE → SNAP → COMPUTE)
        self.phase_indicator = PhaseIndicator(accent=C_ACCENT)
        lay.addWidget(self.phase_indicator)

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
        if not running:
            self.phase_indicator.reset()

    def set_phase(self, idx: int, total: int, phase: str) -> None:
        self.phase_indicator.set_phase(idx, total, phase)

    def get_move_timeout_ms(self) -> int:
        return int(round(float(self.spin_timeout.value()) * 1000.0))

    def is_save_spe_enabled(self) -> bool:
        return bool(self.chk_save_spe.isChecked())
