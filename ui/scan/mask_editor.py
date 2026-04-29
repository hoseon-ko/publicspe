"""ui/scan/mask_editor.py
스캔 분석 무시 마스크 편집 다이얼로그.
이미지 위에 사각형 영역을 드래그로 그리고 클릭/Delete로 삭제한다.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QBrush, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout,
)


# ── 커스텀 뷰 ─────────────────────────────────────────────────────────────────

class _MaskView(QGraphicsView):
    """드래그로 마스크 사각형을 그리고 클릭으로 삭제하는 뷰.

    - 왼쪽 드래그 (빈 공간): 새 마스크 사각형 추가
    - 왼쪽 클릭 (기존 사각형 위): 선택 (주황색으로 강조)
    - Delete / Backspace: 선택된 사각형 삭제
    씬 좌표 = 이미지 픽셀 좌표 (set_image() 호출 후)
    """

    _COLOR_NORMAL = QColor(255, 60,  60,  90)
    _COLOR_SEL    = QColor(255, 160, 60, 160)
    _PEN_NORMAL   = QPen(QColor(255, 80, 80, 200), 1.5, Qt.PenStyle.DashLine)
    _PEN_SEL      = QPen(QColor(255, 200, 60, 255), 2.0, Qt.PenStyle.SolidLine)
    _PEN_PREVIEW  = QPen(QColor(255, 80, 80, 160), 1.0, Qt.PenStyle.DashLine)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._img_item: QGraphicsPixmapItem | None = None
        self._mask_items: list[QGraphicsRectItem] = []
        self._selected: QGraphicsRectItem | None = None
        self._drag_start: QPointF | None = None
        self._preview: QGraphicsRectItem | None = None
        self._img_w = 1
        self._img_h = 1

        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet("background:#080e1e; border:1px solid #1a3a60;")

    # ── 공개 API ─────────────────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        """ndarray (H×W 또는 H×W×3) → 씬 배경 설정."""
        self._scene.clear()
        self._img_item = None
        self._mask_items.clear()
        self._selected = None

        h, w = image.shape[:2]
        self._img_w = w
        self._img_h = h

        img8 = image
        if image.dtype != np.uint8:
            vmax = float(image.max()) or 1.0
            img8 = (image / vmax * 255).astype(np.uint8)

        if img8.ndim == 2:
            qimg = QImage(img8.data, w, h, w, QImage.Format.Format_Grayscale8)
        else:
            rgb = np.ascontiguousarray(img8[:, :, :3])
            qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)

        pix = QPixmap.fromImage(qimg.copy())
        self._img_item = self._scene.addPixmap(pix)
        self._img_item.setZValue(-1)
        self._scene.setSceneRect(0, 0, w, h)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def load_rects(self, rects: list[tuple[int, int, int, int]]):
        """기존 마스크 사각형 목록을 로드한다 [(x1,y1,x2,y2), ...]."""
        for (x1, y1, x2, y2) in rects:
            self._add_rect_item(QRectF(x1, y1, x2 - x1, y2 - y1))

    def get_rects(self) -> list[tuple[int, int, int, int]]:
        """현재 마스크 목록을 이미지 픽셀 좌표로 반환 [(x1,y1,x2,y2), ...]."""
        result = []
        for item in self._mask_items:
            r = item.rect()
            x1 = max(0, int(r.x()))
            y1 = max(0, int(r.y()))
            x2 = min(self._img_w, int(r.x() + r.width()))
            y2 = min(self._img_h, int(r.y() + r.height()))
            if x2 > x1 and y2 > y1:
                result.append((x1, y1, x2, y2))
        return result

    def mask_count(self) -> int:
        return len(self._mask_items)

    def delete_selected(self):
        if self._selected is not None:
            self._scene.removeItem(self._selected)
            if self._selected in self._mask_items:
                self._mask_items.remove(self._selected)
            self._selected = None

    def clear_all(self):
        for item in list(self._mask_items):
            self._scene.removeItem(item)
        self._mask_items.clear()
        self._selected = None

    # ── 내부 헬퍼 ────────────────────────────────────────────────────

    def _add_rect_item(self, rect: QRectF) -> QGraphicsRectItem:
        item = self._scene.addRect(rect, self._PEN_NORMAL, QBrush(self._COLOR_NORMAL))
        item.setZValue(1)
        self._mask_items.append(item)
        return item

    def _select(self, item: QGraphicsRectItem | None):
        if self._selected is not None:
            self._selected.setPen(self._PEN_NORMAL)
            self._selected.setBrush(QBrush(self._COLOR_NORMAL))
        self._selected = item
        if item is not None:
            item.setPen(self._PEN_SEL)
            item.setBrush(QBrush(self._COLOR_SEL))

    # ── 이벤트 ───────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._img_item:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            sp = self.mapToScene(event.pos())
            # 기존 마스크 위 클릭 → 선택
            hit = None
            for item in reversed(self._mask_items):
                if item.rect().contains(sp):
                    hit = item
                    break
            if hit is not None:
                self._select(hit)
            else:
                self._select(None)
                self._drag_start = sp
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            sp = self.mapToScene(event.pos())
            r = QRectF(self._drag_start, sp).normalized()
            if self._preview is not None:
                self._preview.setRect(r)
            else:
                self._preview = self._scene.addRect(
                    r,
                    self._PEN_PREVIEW,
                    QBrush(QColor(255, 50, 50, 40)),
                )
                self._preview.setZValue(2)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_start is not None and event.button() == Qt.MouseButton.LeftButton:
            sp = self.mapToScene(event.pos())
            r = QRectF(self._drag_start, sp).normalized()
            if self._preview is not None:
                self._scene.removeItem(self._preview)
                self._preview = None
            if r.width() > 4 and r.height() > 4:
                self._add_rect_item(r)
            self._drag_start = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        super().keyPressEvent(event)


# ── 다이얼로그 ────────────────────────────────────────────────────────────────

_BTN = lambda c: (
    f"QPushButton {{ background:#0d1a2e; color:{c}; border:1px solid {c};"
    f"border-radius:3px; font-family:'Segoe UI'; font-size:13px; padding:5px 12px; }}"
    f"QPushButton:hover {{ background:#1a3048; }}"
    f"QPushButton:disabled {{ color:#2a3a50; border-color:#1a2a40; }}"
)


class MaskEditorDialog(QDialog):
    """이미지 위에 사각형 무시 영역을 그리고 편집하는 다이얼로그."""

    def __init__(self, image: np.ndarray,
                 existing_rects: list[tuple[int, int, int, int]] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("무시 마스크 편집")
        self.setMinimumSize(720, 520)
        self.setStyleSheet("background:#0a0f1e; color:#c0d0ff;")

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        lbl_info = QLabel(
            "드래그: 새 영역 추가  ·  클릭: 선택  ·  Delete / Backspace: 선택 삭제"
        )
        lbl_info.setStyleSheet(
            "color:#8090b0; font-family:'Segoe UI'; font-size:12px; padding:2px 0;"
        )
        layout.addWidget(lbl_info)

        self._view = _MaskView()
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._view)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._lbl_count = QLabel("0 영역")
        self._lbl_count.setStyleSheet(
            "color:#4ecdc4; font-family:'Courier New'; font-size:14px; min-width:60px;"
        )

        self._btn_del   = QPushButton("선택 삭제")
        btn_clear       = QPushButton("전체 초기화")
        btn_ok          = QPushButton("적용")
        btn_cancel      = QPushButton("취소")

        self._btn_del.setStyleSheet(_BTN("#ffe66d"))
        btn_clear.setStyleSheet(_BTN("#e94560"))
        btn_ok.setStyleSheet(_BTN("#4ecdc4"))
        btn_cancel.setStyleSheet(_BTN("#8090b0"))

        self._btn_del.clicked.connect(self._on_delete)
        btn_clear.clicked.connect(self._on_clear)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self._lbl_count)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_del)
        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self._view.set_image(image)
        if existing_rects:
            self._view.load_rects(existing_rects)
        self._update_count()

        # 씬 변경 시마다 카운트 갱신
        self._view._scene.changed.connect(self._update_count)

    def _on_delete(self):
        self._view.delete_selected()
        self._update_count()

    def _on_clear(self):
        self._view.clear_all()
        self._update_count()

    def _update_count(self, _=None):
        n = self._view.mask_count()
        self._lbl_count.setText(f"{n} 영역")
        self._btn_del.setEnabled(n > 0)

    def get_rects(self) -> list[tuple[int, int, int, int]]:
        return self._view.get_rects()
