"""Ring BG ROI 시각적 오버레이.

BoxROI(신호)를 기준으로 gap+thickness 만큼 확장된 ring 영역 경계를
뷰어 씬에 두 개의 비인터랙티브 QGraphicsRectItem으로 표시한다.

- 외곽 사각형 (DashLine + 연한 채우기): 링 배경 BBox 전체
- 내곽 사각형 (DotLine): 신호 ROI + gap 제외 경계
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPen, QColor, QBrush
from PyQt6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsItem


class RingBGOverlay:
    """뷰어 씬에 Ring BG 경계를 표시하는 오버레이."""

    COLOR = "#38bdf8"

    def __init__(self, scene: QGraphicsScene):
        self._scene = scene

        # ── 외곽 bbox (링 전체 범위) ───────────────────────────────────
        self._outer = QGraphicsRectItem()
        pen_out = QPen(QColor(self.COLOR), 1, Qt.PenStyle.DashLine)
        pen_out.setCosmetic(True)
        self._outer.setPen(pen_out)
        self._outer.setBrush(QBrush(QColor(56, 189, 248, 18)))
        self._outer.setZValue(4)
        self._outer.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._outer.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._outer.hide()
        scene.addItem(self._outer)

        # ── 내곽 경계 (신호 ROI + gap 제외 영역) ─────────────────────
        self._inner = QGraphicsRectItem()
        pen_in = QPen(QColor(self.COLOR), 1, Qt.PenStyle.DotLine)
        pen_in.setCosmetic(True)
        self._inner.setPen(pen_in)
        self._inner.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._inner.setZValue(4)
        self._inner.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._inner.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._inner.hide()
        scene.addItem(self._inner)

    def update(self, x0: float, y0: float, x1: float, y1: float,
               gap: int, thickness: int) -> None:
        """신호 ROI 좌표(x0,y0,x1,y1)와 파라미터로 오버레이 갱신."""
        ix0, iy0 = x0 - gap,       y0 - gap
        ix1, iy1 = x1 + gap,       y1 + gap
        ox0, oy0 = ix0 - thickness, iy0 - thickness
        ox1, oy1 = ix1 + thickness, iy1 + thickness

        self._inner.setRect(QRectF(ix0, iy0, ix1 - ix0, iy1 - iy0))
        self._outer.setRect(QRectF(ox0, oy0, ox1 - ox0, oy1 - oy0))
        self._inner.show()
        self._outer.show()

    def hide(self) -> None:
        self._inner.hide()
        self._outer.hide()

    def remove(self) -> None:
        for item in (self._inner, self._outer):
            if item.scene():
                self._scene.removeItem(item)
