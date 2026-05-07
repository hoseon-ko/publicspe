"""
ui/widgets/auto_splitter.py
더블클릭으로 좌측 패널을 콘텐츠 sizeHint 너비로 자동 리사이즈하는 QSplitter.

사용법:
    splitter = AutoSplitter(Qt.Orientation.Horizontal)
    splitter.addWidget(left_panel)
    splitter.addWidget(right_panel)
    # 더블클릭하면 left_panel.sizeHint().width() 로 자동 스냅
"""

from __future__ import annotations

from PyQt6.QtWidgets import QSplitter, QSplitterHandle
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QMouseEvent


class _AutoHandle(QSplitterHandle):
    """더블클릭 시 index=0 위젯을 sizeHint 너비로 자동 스냅."""

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        sp = self.splitter()
        if sp is None or sp.count() < 2:
            super().mouseDoubleClickEvent(event)
            return

        left = sp.widget(0)
        if left is None:
            super().mouseDoubleClickEvent(event)
            return

        # 콘텐츠 sizeHint 너비 (ScrollArea 안 inner widget 우선)
        hint_w = _content_width(left)

        total = sum(sp.sizes())
        right_w = max(0, total - hint_w)
        sp.setSizes([hint_w, right_w])
        event.accept()


def _content_width(widget) -> int:
    """ScrollArea면 내부 widget의 sizeHint, 아니면 widget 자체 sizeHint."""
    from PyQt6.QtWidgets import QScrollArea, QAbstractScrollArea

    # QScrollArea 안 widget 우선
    inner = None
    if isinstance(widget, QScrollArea):
        inner = widget.widget()

    if inner is not None:
        hint: QSize = inner.sizeHint()
    else:
        hint = widget.sizeHint()

    # 세로 스크롤바 폭 보정 (보통 8~12px)
    sb_w = 0
    if isinstance(widget, QAbstractScrollArea):
        sb = widget.verticalScrollBar()
        if sb and sb.isVisible():
            sb_w = sb.width()

    w = hint.width() + sb_w + 4   # 여유 4px
    return max(w, widget.minimumWidth())


class AutoSplitter(QSplitter):
    """더블클릭 자동 리사이즈를 지원하는 QSplitter."""

    def createHandle(self) -> QSplitterHandle:  # type: ignore[override]
        return _AutoHandle(self.orientation(), self)
