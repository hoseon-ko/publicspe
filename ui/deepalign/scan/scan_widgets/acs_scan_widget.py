"""ACS 6축 키네마틱 스캔 워크플로우 위젯 — 패널과 분리된 standalone UI.

DOF(Tx/Ty/Tz/Rx/Ry/Rz) sweep → KinematicCalc.calculate로 6모터 cal_pos 변환 +
인터락 검증을 위젯 내부에서 처리. 검증된 cal_pos 리스트만 scan_requested로 emit.

  scan_requested(points, settle_ms, avg_frames)에서
  points: list[np.ndarray(6,)] (검증된 6모터 절대 cal_pos, mm)
"""

from __future__ import annotations
from typing import Callable, Optional
import numpy as np

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QSpinBox,
    QDoubleSpinBox, QPushButton, QTextEdit, QLabel, QFrame, QCheckBox,
)
# (QComboBox 는 위에서 import 됨)

from theme.styles import (
    C_ACCENT, C_DANGER, C_TEXT_DIM, Fonts, BTN_SMALL, SPIN_STYLE, COMBO_STYLE, lbl,
)
from core.motor.kinematic_calc import KinematicCalc
from ui.widgets.collapsible_section import CollapsibleSection
from ui.deepalign.scan.scan_widgets._common import (
    label_dim, status_label, apply_status, PhaseIndicator,
)

_ACS_ACCENT = "#aa7acc"


class AcsScanWidget(QWidget):
    """ACS 키네마틱 스캔 — DOF 1개 sweep + 다른 5 DOF는 baseline.

    baseline DOF는 위젯 자체의 6개 spinbox를 사용 (standalone). 외부에서
    set_baseline_from_panel()을 주입하면 그 값으로 일괄 갱신 가능.
    """

    scan_requested      = pyqtSignal(list, int, int)
    scan_stop_requested = pyqtSignal()
    save_last_requested = pyqtSignal()

    DOF_LABELS   = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]
    DOF_UNITS    = [" mm", " mm", " mm", " mrad", " mrad", " mrad"]
    DOF_DECIMALS = [4, 4, 4, 3, 3, 3]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._calc = KinematicCalc()
        self._baseline_provider: Optional[Callable[[], list[float]]] = None
        self._build_ui()

    def set_baseline_provider(self, fn: Callable[[], list[float]]) -> None:
        """외부에서 baseline DOF 6값을 제공받는 콜백을 등록 (Optional)."""
        self._baseline_provider = fn

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # PicoCard와 동일한 외곽 카드 스타일 (ACS는 보라색 accent)
        card = QFrame()
        card.setObjectName("motionCard")
        card.setStyleSheet(f"""
            QFrame#motionCard {{
                background: #0f1729;
                border: 1px solid {_ACS_ACCENT};
                border-radius: 6px;
            }}
        """)
        root.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        # Title — PicoCard 대제목과 동일 톤 (ACS는 보라색)
        title = QLabel("▾  ACS KINEMATIC SCAN")
        title.setStyleSheet(
            f"color: {_ACS_ACCENT}; font-family: '{Fonts.MONO}';"
            f" font-size: 20px; font-weight: bold; letter-spacing: 2px;"
            f" background: transparent; border: none;"
        )
        lay.addWidget(title)

        def _lbl(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(lbl(C_TEXT_DIM, mono=True) + " background: transparent; border: none;")
            return l

        # ─ Sweep 파라미터 섹션 ─
        sec_params = CollapsibleSection("SWEEP PARAMETERS", accent=_ACS_ACCENT)
        params_l = sec_params.content_layout()

        grid = QGridLayout()
        grid.setSpacing(4)

        self.cb_dof = QComboBox()
        for i, n in enumerate(self.DOF_LABELS):
            self.cb_dof.addItem(n, i)
        self.cb_dof.setStyleSheet(COMBO_STYLE)
        self.cb_dof.currentIndexChanged.connect(self._on_dof_changed)

        def _dspin(decs=4) -> QDoubleSpinBox:
            # sweep 범위는 ±10 (mm/mrad). 실제 stroke 보다 약간 여유.
            # 큰 값 실수 입력 방지 — 필요시 baseline DOF 로 base 이동 후 sweep.
            s = QDoubleSpinBox()
            s.setRange(-10.0, 10.0); s.setDecimals(decs)
            s.setStyleSheet(SPIN_STYLE)
            return s

        def _ispin(lo, hi, val) -> QSpinBox:
            s = QSpinBox(); s.setRange(lo, hi); s.setValue(val); s.setStyleSheet(SPIN_STYLE)
            return s

        self.spin_start = _dspin()
        self.spin_start.setValue(-0.1)
        self.spin_end   = _dspin()
        self.spin_end.setValue(0.1)
        self.spin_n       = _ispin(2, 999, 5)
        self.spin_settle  = _ispin(0, 10000, 500)
        self.spin_avg     = _ispin(1, 32, 1)
        self.spin_timeout = QDoubleSpinBox()
        self.spin_timeout.setRange(1.0, 120.0); self.spin_timeout.setDecimals(1)
        self.spin_timeout.setSingleStep(1.0); self.spin_timeout.setValue(30.0)
        self.spin_timeout.setSuffix(" s"); self.spin_timeout.setStyleSheet(SPIN_STYLE)

        grid.addWidget(_lbl("DOF"),          0, 0); grid.addWidget(self.cb_dof,       0, 1)
        grid.addWidget(_lbl("N points"),     0, 2); grid.addWidget(self.spin_n,       0, 3)
        grid.addWidget(_lbl("Start"),        1, 0); grid.addWidget(self.spin_start,   1, 1)
        grid.addWidget(_lbl("End"),          1, 2); grid.addWidget(self.spin_end,     1, 3)
        grid.addWidget(_lbl("Settle ms"),    2, 0); grid.addWidget(self.spin_settle,  2, 1)
        grid.addWidget(_lbl("Avg"),          2, 2); grid.addWidget(self.spin_avg,     2, 3)
        grid.addWidget(_lbl("Move Timeout"), 3, 0); grid.addWidget(self.spin_timeout, 3, 1)
        params_l.addLayout(grid)
        lay.addWidget(sec_params)

        # ─ Baseline DOF 6 입력 (다른 5 DOF용) ─
        sec_base = CollapsibleSection("BASELINE DOF (다른 5축 고정값)", accent=_ACS_ACCENT)
        base_l = sec_base.content_layout()

        bl_grid = QGridLayout()
        bl_grid.setSpacing(4)
        self.spin_baseline: list[QDoubleSpinBox] = []
        for i, (n, suf, decs) in enumerate(zip(self.DOF_LABELS, self.DOF_UNITS, self.DOF_DECIMALS)):
            sp = QDoubleSpinBox()
            sp.setRange(-500.0, 500.0); sp.setDecimals(decs); sp.setValue(0.0)
            sp.setSuffix(suf); sp.setStyleSheet(SPIN_STYLE)
            self.spin_baseline.append(sp)
            r, c = divmod(i, 3)
            bl_grid.addWidget(_lbl(n), r, c * 2)
            bl_grid.addWidget(sp,      r, c * 2 + 1)
        base_l.addLayout(bl_grid)

        # SYNC 버튼 — BTN_SMALL 사용 (PicoCard 톤)
        self.btn_sync_baseline = QPushButton("SYNC BASELINE FROM PANEL")
        self.btn_sync_baseline.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_TEXT_DIM))
        self.btn_sync_baseline.clicked.connect(self._on_sync_baseline)
        base_l.addWidget(self.btn_sync_baseline)
        lay.addWidget(sec_base)

        spe_row = QHBoxLayout(); spe_row.setSpacing(6)
        self.cb_spe_mode = QComboBox()
        self.cb_spe_mode.addItems(["💾 SPE: Off", "💾 SPE: Auto-save", "💾 SPE: Manual"])
        self.cb_spe_mode.setCurrentIndex(0)
        self.cb_spe_mode.setStyleSheet(
            f"QComboBox {{ background:#080e1e; color:#c0d0ff; border:1px solid {_ACS_ACCENT};"
            f" border-radius:3px; font-family:'{Fonts.MONO}'; font-size:11px; padding:2px 6px; }}"
            f"QComboBox::drop-down {{ border:none; }}"
            f"QComboBox QAbstractItemView {{ background:#0f1729; color:#c0d0ff; }}"
        )
        self.btn_save_last = QPushButton("💾 Save Last")
        self.btn_save_last.setEnabled(False)
        self.btn_save_last.setStyleSheet(BTN_SMALL.replace(C_ACCENT, _ACS_ACCENT))
        self.btn_save_last.clicked.connect(self.save_last_requested)
        spe_row.addWidget(self.cb_spe_mode, 1)
        spe_row.addWidget(self.btn_save_last)
        lay.addLayout(spe_row)

        # ─ Start/Stop — BTN_SMALL ─
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("SCAN START")
        self.btn_stop  = QPushButton("SCAN STOP")
        # ACS는 보라 accent
        self.btn_start.setStyleSheet(BTN_SMALL.replace(C_ACCENT, _ACS_ACCENT))
        self.btn_stop.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self.scan_stop_requested)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        lay.addLayout(btn_row)

        self.phase_indicator = PhaseIndicator(accent=_ACS_ACCENT)
        lay.addWidget(self.phase_indicator)

        self.lbl_status = status_label()
        lay.addWidget(self.lbl_status)

        # ─ Preview / 위반 로그 ─
        self.txt_preview = QTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setFixedHeight(120)
        self.txt_preview.setStyleSheet(f"""
            QTextEdit {{
                background: #050a15; color: #c0d0ff;
                border: 1px solid {_ACS_ACCENT}; border-radius: 3px;
                font-family: '{Fonts.MONO}'; font-size: 11px;
            }}
        """)
        self.txt_preview.setPlaceholderText("스캔 시작 전 cal_pos 미리보기 / 인터락 위반 로그")
        lay.addWidget(self.txt_preview)

        # 초기 단위 동기화
        self._on_dof_changed(0)

    # ── 동작 ──────────────────────────────────────────────────────────────
    def _on_dof_changed(self, idx: int) -> None:
        if not (0 <= idx < 6):
            return
        suf  = self.DOF_UNITS[idx]
        decs = self.DOF_DECIMALS[idx]
        for sp in (self.spin_start, self.spin_end):
            sp.setSuffix(suf)
            sp.setDecimals(decs)
            sp.setSingleStep(0.01 if decs >= 3 else 0.1)

    def _on_sync_baseline(self) -> None:
        if self._baseline_provider is None:
            apply_status(self.lbl_status, "baseline provider 미주입", "warn")
            return
        try:
            vals = self._baseline_provider()
        except Exception as e:
            apply_status(self.lbl_status, f"baseline 조회 실패: {e}", "err")
            return
        if not vals or len(vals) != 6:
            apply_status(self.lbl_status, f"baseline 6개 필요 (got {len(vals) if vals else 0})", "err")
            return
        for sp, v in zip(self.spin_baseline, vals):
            sp.setValue(float(v))
        apply_status(self.lbl_status, "baseline 동기화 완료", "ok")

    def _on_start(self) -> None:
        dof_idx = int(self.cb_dof.currentData())
        n       = int(self.spin_n.value())
        start   = float(self.spin_start.value())
        end     = float(self.spin_end.value())
        base_dof = [float(s.value()) for s in self.spin_baseline]

        sweep = np.linspace(start, end, n)
        # point = (cal_pos, dof_dict) — worker 가 record 에 양쪽 모두 기록
        points_payload: list[tuple] = []
        violations: list[tuple[int, float, list[str]]] = []
        for i, v in enumerate(sweep, 1):
            dof = base_dof.copy()
            dof[dof_idx] = float(v)
            trans, rotate = dof[:3], dof[3:]
            cal, _ball, ok, vio = self._calc.calculate(trans, rotate)
            if cal is None:
                apply_status(self.lbl_status,
                             f"변환 실패 (idx={i}): {vio[0] if vio else '?'}", "err")
                return
            if not ok:
                violations.append((i, float(v), vio))
                continue
            dof_dict = {name: float(val) for name, val in zip(self.DOF_LABELS, dof)}
            points_payload.append((cal, dof_dict))

        if violations:
            self.txt_preview.setPlainText(
                f"❌ 인터락 위반 — {len(violations)}/{n} 점\n"
                + "\n".join(
                    f"  idx={i:>3}  {self.DOF_LABELS[dof_idx]}={v:+.4f}: {vio[0]}"
                    for i, v, vio in violations[:10]
                )
                + ("\n  ..." if len(violations) > 10 else "")
                + "\n\n→ 범위/스텝을 줄여 다시 시도하세요."
            )
            apply_status(self.lbl_status,
                         f"인터락 위반 {len(violations)}/{n}점 — 중단", "err")
            return

        # 미리보기
        label = self.DOF_LABELS[dof_idx]
        unit  = self.DOF_UNITS[dof_idx].strip()
        first_cal = points_payload[0][0]
        last_cal  = points_payload[-1][0]
        lines = [
            f"✓ 키네마틱 스캔 준비: {label} {start:+.4f} → {end:+.4f} {unit}, N={n}",
            "  baseline DOF: " + ", ".join(f"{n_}={v:+.4f}" for n_, v in zip(self.DOF_LABELS, base_dof)),
            "  CalPos 첫/끝 점 (Y1 Z1 X1 Z2 Y2 Z3):",
            "    [0]   " + " ".join(f"{x:+.4f}" for x in first_cal),
        ]
        if len(points_payload) > 1:
            lines.append(f"    [{len(points_payload)-1}]   " + " ".join(f"{x:+.4f}" for x in last_cal))
        self.txt_preview.setPlainText("\n".join(lines))

        self.scan_requested.emit(
            points_payload,
            int(self.spin_settle.value()),
            int(self.spin_avg.value()),
        )

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

    def get_spe_save_mode(self) -> str:
        return ("off", "auto", "manual")[max(0, self.cb_spe_mode.currentIndex())]

    def is_save_spe_enabled(self) -> bool:
        return self.get_spe_save_mode() != "off"

    def set_save_last_enabled(self, enabled: bool) -> None:
        self.btn_save_last.setEnabled(bool(enabled))
