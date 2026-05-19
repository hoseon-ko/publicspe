"""스캔 위젯 3종 공용 스타일/헬퍼."""

from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel, QSpinBox, QDoubleSpinBox, QPushButton, QFrame,
    QWidget, QHBoxLayout,
)
from theme.styles import C_ACCENT, C_DANGER, C_TEXT_DIM, Fonts

# Phase 식별자 — _scan_base.PHASE_* 와 동일 (의존성 분리 목적으로 여기 재선언)
PHASE_LABELS = [
    ("move",    "MOVE"),
    ("settle",  "SETTLE"),
    ("snap",    "SNAP"),
    ("compute", "COMPUTE"),
]


SPIN_QSS = f"""
    QSpinBox, QDoubleSpinBox {{
        background: #080e1e; border: 1px solid #0f3460;
        color: #c0d0ff; border-radius: 3px;
        font-family: '{Fonts.MONO}'; font-size: 13px; padding: 2px 4px;
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {C_ACCENT}; }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 14px; border: none; background: #0d1e38;
    }}
"""

EDIT_QSS = f"""
    QComboBox {{
        background: #080e1e; border: 1px solid #0f3460;
        color: #c0d0ff; border-radius: 3px;
        font-family: '{Fonts.MONO}'; font-size: 13px; padding: 2px 6px;
    }}
    QComboBox:focus {{ border-color: {C_ACCENT}; }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox QAbstractItemView {{
        background: #0d1e38; color: #c0d0ff;
        border: 1px solid #0f3460; selection-background-color: #1a3a60;
    }}
"""

def btn_qss(color: str) -> str:
    return f"""
        QPushButton {{
            background: transparent; color: {color};
            border: 1px solid {color}; border-radius: 3px;
            font-family: '{Fonts.MONO}'; font-size: 12px;
            font-weight: bold; padding: 5px 10px;
        }}
        QPushButton:hover {{ background: {color}22; }}
        QPushButton:disabled {{ color: #304060; border-color: #1a2840; }}
    """

def section_frame(title: str, accent: str = C_ACCENT) -> tuple[QFrame, "QVBoxLayout"]:
    """공통 외곽 프레임 + 타이틀 — 반환: (frame, content_layout)."""
    from PyQt6.QtWidgets import QFrame, QVBoxLayout

    frame = QFrame()
    frame.setStyleSheet(f"""
        QFrame {{
            background: #0f1729;
            border: 1px solid #1a4060;
            border-left: 3px solid {accent};
            border-radius: 4px;
        }}
    """)
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(8, 6, 8, 8)
    outer.setSpacing(6)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(
        f"color: {accent}; font-family: '{Fonts.MONO}';"
        f" font-size: 11px; font-weight: bold;"
        f" letter-spacing: 2px; border: none; background: transparent;"
    )
    outer.addWidget(title_lbl)
    return frame, outer

def label_dim(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(
        f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
        f" font-size: 11px; border: none; background: transparent;"
    )
    return l

def status_label() -> QLabel:
    l = QLabel("idle")
    l.setAlignment(Qt.AlignmentFlag.AlignCenter)
    l.setStyleSheet(
        f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
        f" font-size: 12px; border: none; background: rgba(0,0,0,0.2);"
        f" border-radius: 3px; padding: 4px;"
    )
    return l


_STATUS_COLORS = {
    "info": "#a0b0d0", "ok": C_ACCENT, "warn": "#facc15", "err": C_DANGER,
}


class PhaseIndicator(QWidget):
    """스캔 진행 단계 시각화 — MOVE → SETTLE → SNAP → COMPUTE.

    현재 phase 만 accent 색으로 강조, 나머지는 dim. progress 카운터 (idx/total)
    까지 한 줄에 함께 표시.

    UI 사용:
        ind = PhaseIndicator(accent=C_ACCENT)
        ind.set_phase(idx=3, total=10, phase="snap")  # 도트 갱신
        ind.reset()                                    # 모두 dim, "—/—"
    """

    _DOT_ON  = "●"
    _DOT_OFF = "○"

    def __init__(self, accent: str = C_ACCENT, parent=None):
        super().__init__(parent)
        self._accent = accent
        self._dim    = "#3a4a64"
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(8)

        # 좌측: idx/total 카운터
        self.lbl_count = QLabel("—/—")
        self.lbl_count.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
            f" font-size: 11px; font-weight: bold;"
            f" background: transparent; border: none;"
            f" min-width: 44px;"
        )
        root.addWidget(self.lbl_count)

        # 우측: 4 phase 도트 + 라벨
        self._phase_widgets: dict[str, tuple[QLabel, QLabel]] = {}
        for key, label in PHASE_LABELS:
            dot = QLabel(self._DOT_OFF)
            dot.setStyleSheet(
                f"color: {self._dim}; font-family: '{Fonts.MONO}';"
                f" font-size: 12px;"
                f" background: transparent; border: none;"
            )
            txt = QLabel(label)
            txt.setStyleSheet(
                f"color: {self._dim}; font-family: '{Fonts.MONO}';"
                f" font-size: 10px; letter-spacing: 1px;"
                f" background: transparent; border: none;"
            )
            root.addWidget(dot)
            root.addWidget(txt)
            self._phase_widgets[key] = (dot, txt)
        root.addStretch(1)

    # ── 외부 API ──────────────────────────────────────────────────────────

    def set_phase(self, idx: int, total: int, phase: str) -> None:
        """현재 phase 강조. phase ∈ {"move","settle","snap","compute","done"}."""
        self.lbl_count.setText(f"{idx}/{total}")
        # done 은 4단계 모두 ok 처리, 그 외엔 해당 phase 만 강조
        for key, (dot, txt) in self._phase_widgets.items():
            if phase == "done" or key == phase:
                dot.setText(self._DOT_ON)
                dot.setStyleSheet(
                    f"color: {self._accent}; font-family: '{Fonts.MONO}';"
                    f" font-size: 12px;"
                    f" background: transparent; border: none;"
                )
                txt.setStyleSheet(
                    f"color: {self._accent}; font-family: '{Fonts.MONO}';"
                    f" font-size: 10px; letter-spacing: 1px; font-weight: bold;"
                    f" background: transparent; border: none;"
                )
            else:
                dot.setText(self._DOT_OFF)
                dot.setStyleSheet(
                    f"color: {self._dim}; font-family: '{Fonts.MONO}';"
                    f" font-size: 12px;"
                    f" background: transparent; border: none;"
                )
                txt.setStyleSheet(
                    f"color: {self._dim}; font-family: '{Fonts.MONO}';"
                    f" font-size: 10px; letter-spacing: 1px;"
                    f" background: transparent; border: none;"
                )

    def reset(self) -> None:
        """모든 도트를 idle 상태로."""
        self.lbl_count.setText("—/—")
        for _key, (dot, txt) in self._phase_widgets.items():
            dot.setText(self._DOT_OFF)
            dot.setStyleSheet(
                f"color: {self._dim}; font-family: '{Fonts.MONO}';"
                f" font-size: 12px;"
                f" background: transparent; border: none;"
            )
            txt.setStyleSheet(
                f"color: {self._dim}; font-family: '{Fonts.MONO}';"
                f" font-size: 10px; letter-spacing: 1px;"
                f" background: transparent; border: none;"
            )

def apply_status(label: QLabel, msg: str, kind: str = "info") -> None:
    color = _STATUS_COLORS.get(kind, _STATUS_COLORS["info"])
    label.setText(msg)
    label.setStyleSheet(
        f"color: {color}; font-family: '{Fonts.MONO}';"
        f" font-size: 12px; border: none; background: rgba(0,0,0,0.2);"
        f" border-radius: 3px; padding: 4px;"
    )
