"""
ui/widgets/collapsible_section.py
▶/▼ 클릭으로 접기/펼치기 가능한 섹션 위젯.

사용법:
    sec = CollapsibleSection("CAMERA", accent=C_ACCENT)
    sec.add_widget(some_widget)
    sec.add_layout(some_layout)
    layout.addWidget(sec)

    # 초기 접힘
    sec2 = CollapsibleSection("ADC", collapsed=True)
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy,
)
from PyQt6.QtGui import QCursor

from theme.styles import (
    Fonts, Sizes,
    C_ACCENT, C_DANGER, C_BORDER, C_BG_MED, C_BG_DARK, C_BG_DEEP,
)


class CollapsibleSection(QWidget):
    """
    접기/펼치기 가능한 섹션.

    ┌─ ▼  SECTION TITLE ──────────────────────┐  ← 헤더 클릭으로 토글
    │  content area                            │
    └──────────────────────────────────────────┘
    """
    toggled = pyqtSignal(bool)  # 접힘 상태 변경 시 발생 (True: 접힘, False: 펼침)

    def __init__(
        self,
        title: str,
        accent: str = C_DANGER,
        collapsed: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._collapsed = collapsed
        self._accent = accent
        self._anim: QPropertyAnimation | None = None

        self._build_ui(title, accent)
        if collapsed:
            self._content_wrap.setVisible(False)
            self._content_wrap.setMaximumHeight(0)
        else:
            self._content_wrap.setMaximumHeight(16_777_215)

    # ── UI 구성 ───────────────────────────────────────────────────────

    def _build_ui(self, title: str, accent: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 2)
        root.setSpacing(0)

        # ── 헤더 (QFrame + mousePressEvent) ──────────────────────────
        self._header = QFrame()
        self._header.setFixedHeight(24)
        self._header.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._header.setStyleSheet(
            f"QFrame {{"
            f"  background: {C_BG_MED};"
            f"  border: 1px solid {C_BORDER};"
            f"  border-radius: 4px;"
            f"}}"
            f"QFrame:hover {{"
            f"  background: #12223e;"
            f"  border-color: {accent};"
            f"}}"
        )

        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(8, 0, 8, 0)
        h_layout.setSpacing(6)

        self._arrow = QLabel("▶" if self._collapsed else "▼")
        self._arrow.setFixedWidth(12)
        self._arrow.setStyleSheet(
            f"color: {accent}; font-size: 9px;"
            f"font-family: '{Fonts.MONO}';"
            "background: transparent; border: none;"
        )

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            f"color: {accent};"
            f"font-family: '{Fonts.MONO}';"
            f"font-size: {Sizes.CTRL};"
            "font-weight: bold;"
            "letter-spacing: 2px;"
            "background: transparent; border: none;"
        )

        h_layout.addWidget(self._arrow)
        h_layout.addWidget(self._title_lbl, 1)

        # 헤더 클릭 → toggle (QFrame은 clicked 시그널 없음 → mousePressEvent 오버라이드)
        self._header.mousePressEvent = lambda ev: self.toggle()

        root.addWidget(self._header)

        # ── 콘텐츠 래퍼 ──────────────────────────────────────────────
        self._content_wrap = QWidget()
        self._content_wrap.setStyleSheet(
            f"QWidget {{"
            f"  background: {C_BG_DARK};"
            f"  border-left:   1px solid {C_BORDER};"
            f"  border-right:  1px solid {C_BORDER};"
            f"  border-bottom: 1px solid {C_BORDER};"
            f"  border-radius: 0 0 4px 4px;"
            f"}}"
        )
        self._content_layout = QVBoxLayout(self._content_wrap)
        self._content_layout.setContentsMargins(8, 6, 8, 8)
        self._content_layout.setSpacing(4)

        root.addWidget(self._content_wrap)

    # ── 콘텐츠 추가 API ───────────────────────────────────────────────

    def add_widget(self, widget: QWidget) -> QWidget:
        self._content_layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self._content_layout.addLayout(layout)

    def add_stretch(self) -> None:
        self._content_layout.addStretch()

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    # ── 접기/펼치기 ───────────────────────────────────────────────────

    def toggle(self):
        self.set_collapsed(not self._collapsed, animated=True)

    def set_collapsed(self, collapsed: bool, animated: bool = False):
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self._arrow.setText("▶" if collapsed else "▼")
        self.toggled.emit(collapsed)

        if animated:
            self._run_animation(target_h=0 if collapsed else -1)
        else:
            self._content_wrap.setVisible(not collapsed)
            self._content_wrap.setMaximumHeight(0 if collapsed else 16_777_215)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _run_animation(self, target_h: int):
        """target_h == -1 이면 natural height 로 펼침."""
        if target_h == -1:
            self._content_wrap.setVisible(True)
            self._content_wrap.setMaximumHeight(16_777_215)
            natural = self._content_wrap.sizeHint().height()
            target_h = max(natural, 30)
            start_h = 0
        else:
            start_h = self._content_wrap.height()

        self._content_wrap.setVisible(True)

        anim = QPropertyAnimation(self._content_wrap, b"maximumHeight", self)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.setStartValue(start_h)
        anim.setEndValue(target_h)

        if target_h == 0:
            anim.finished.connect(lambda: self._content_wrap.setVisible(False))

        anim.start()
        self._anim = anim   # GC 방지
