"""스캔 위젯 3종 공용 스타일/헬퍼."""

from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSpinBox, QDoubleSpinBox, QPushButton, QFrame
from theme.styles import C_ACCENT, C_DANGER, C_TEXT_DIM, Fonts


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

def apply_status(label: QLabel, msg: str, kind: str = "info") -> None:
    color = _STATUS_COLORS.get(kind, _STATUS_COLORS["info"])
    label.setText(msg)
    label.setStyleSheet(
        f"color: {color}; font-family: '{Fonts.MONO}';"
        f" font-size: 12px; border: none; background: rgba(0,0,0,0.2);"
        f" border-radius: 3px; padding: 4px;"
    )
