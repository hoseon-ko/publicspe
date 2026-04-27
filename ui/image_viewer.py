"""
image_viewer.py
QGraphicsView 기반 이미지 뷰어
- 픽셀 눈금자 (X/Y)
- 휠 줌 + 스크롤바 이동
- 크로스헤어 (토글)
- 클릭 마커 (라스트 포인트)
- ROI 드래그 (Line / Box / Histogram)
- 컬러맵 numpy 직접 적용
"""

import numpy as np
from ui.roi_items import LineROI, BoxROI, HistROI
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsLineItem, QGraphicsRectItem,
    QSizePolicy, QToolButton
)
from PyQt6.QtCore import pyqtSignal, Qt, QRectF
from PyQt6.QtGui import (
    QPixmap, QImage, QPen, QColor, QBrush, QPainter,
    QWheelEvent, QMouseEvent, QFont, QTransform
)

from typing import Optional, Union

# ─────────────────────────────────────────────────────────────────────────────
# 컬러맵
# ─────────────────────────────────────────────────────────────────────────────

def apply_colormap(image: np.ndarray, cmap: str = 'jet') -> np.ndarray:
    """2D float 이미지 → RGBA uint8"""
    f = image.astype(np.float64)
    vmin, vmax = f.min(), f.max()
    if vmax > vmin:
        f = (f - vmin) / (vmax - vmin)
    else:
        f = np.zeros_like(f)

    if cmap == 'jet':
        r = np.clip(1.5 - np.abs(4.0 * f - 3.0), 0, 1)
        g = np.clip(1.5 - np.abs(4.0 * f - 2.0), 0, 1)
        b = np.clip(1.5 - np.abs(4.0 * f - 1.0), 0, 1)
    elif cmap == 'grey':
        r = g = b = f
    elif cmap == 'hot':
        r = np.clip(f * 3.0, 0, 1)
        g = np.clip(f * 3.0 - 1.0, 0, 1)
        b = np.clip(f * 3.0 - 2.0, 0, 1)
    elif cmap == 'viridis':
        r = np.clip(0.267 + 0.005 * f + 2.33 * f**2 - 1.98 * f**3, 0, 1)
        g = np.clip(0.005 + 1.40 * f - 0.55 * f**2, 0, 1)
        b = np.clip(0.329 + 1.50 * f - 1.85 * f**2, 0, 1)
    elif cmap == 'plasma':
        r = np.clip(0.05 + 2.0 * f - 0.7 * f**2, 0, 1)
        g = np.clip(0.03 * f + 1.2 * f**2 - 0.5 * f**3, 0, 1)
        b = np.clip(0.55 - 0.8 * f + 0.5 * f**2, 0, 1)
    else:
        r = g = b = f

    rgba = np.stack([r, g, b, np.ones_like(f)], axis=-1)
    return (rgba * 255).astype(np.uint8)


def ndarray_to_qpixmap(rgba: np.ndarray) -> QPixmap:
    h, w = rgba.shape[:2]
    rgba_c = np.ascontiguousarray(rgba)
    img = QImage(rgba_c.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())


# ─────────────────────────────────────────────────────────────────────────────
# 눈금자 위젯
# ─────────────────────────────────────────────────────────────────────────────

class RulerWidget(QWidget):
    """픽셀 단위 눈금자"""

    def __init__(self, orientation: str = 'horizontal', parent=None):
        super().__init__(parent)
        self._orientation = orientation  # 'horizontal' | 'vertical'
        self._scale = 1.0       # 픽셀당 화면 픽셀
        self._offset = 0.0      # 스크롤 오프셋 (이미지 픽셀)
        self._img_size = 1000   # 이미지 크기 (픽셀)

        if orientation == 'horizontal':
            self.setFixedHeight(24)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(48)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self.setStyleSheet("background-color: #16213e;")

    def update_transform(self, scale: float, offset: float, img_size: int):
        self._scale = scale
        self._offset = offset
        self._img_size = img_size
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QPen, QFont
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        bg = QColor('#16213e')
        tick_color = QColor('#506080')
        text_color = QColor('#a0a0b0')

        painter.fillRect(self.rect(), bg)
        painter.setPen(QPen(tick_color, 1))

        font = QFont('Segoe UI', 8)
        painter.setFont(font)
        painter.setPen(text_color)

        w = self.width()
        h = self.height()

        # 눈금 간격 결정 (화면 40px 이상 간격)
        target_px = 60
        raw_step = target_px / max(self._scale, 0.001)
        magnitude = 10 ** int(np.log10(max(raw_step, 1)))
        for step in [magnitude, magnitude * 2, magnitude * 5, magnitude * 10]:
            if step * self._scale >= target_px:
                tick_step = step
                break
        else:
            tick_step = magnitude * 10

        # self._offset 은 이미 이미지 픽셀 단위
        start_img = int(self._offset)
        start_tick = (start_img // tick_step) * tick_step

        if self._orientation == 'horizontal':
            painter.setPen(QPen(QColor('#304060'), 1))
            painter.drawLine(0, h - 1, w, h - 1)
            img_px = start_tick
            while True:
                screen_x = int((img_px - self._offset) * self._scale)
                if screen_x > w:
                    break
                if img_px < 0:
                    img_px += tick_step
                    continue
                painter.setPen(QPen(tick_color, 1))
                painter.drawLine(screen_x, h - 8, screen_x, h - 1)
                painter.setPen(text_color)
                painter.drawText(screen_x + 2, h - 10, str(img_px))
                img_px += tick_step
        else:
            painter.setPen(QPen(QColor('#304060'), 1))
            painter.drawLine(w - 1, 0, w - 1, h)
            img_px = start_tick
            while True:
                screen_y = int((img_px - self._offset) * self._scale)
                if screen_y > h:
                    break
                if img_px < 0:
                    img_px += tick_step
                    continue
                painter.setPen(QPen(tick_color, 1))
                painter.drawLine(w - 8, screen_y, w - 1, screen_y)
                painter.setPen(text_color)
                painter.save()
                painter.translate(w - 10, screen_y - 2)
                painter.rotate(-90)
                painter.drawText(0, 0, str(img_px))
                painter.restore()
                img_px += tick_step


# ─────────────────────────────────────────────────────────────────────────────
# 이미지 뷰 (QGraphicsView 기반)
# ─────────────────────────────────────────────────────────────────────────────

class ImageGraphicsView(QGraphicsView):
    """
    확대/축소: 마우스 휠
    이동: 스크롤바
    ROI: 드래그
    크로스헤어: 마우스 이동
    """

    # 시그널
    mouse_moved      = pyqtSignal(float, float)   # 이미지 좌표
    mouse_clicked    = pyqtSignal(float, float)   # 클릭 좌표
    roi_drawn        = pyqtSignal(str, object)    # (mode, pts)
    scale_changed    = pyqtSignal(float, float, float)  # (scale, x_offset, y_offset)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # 렌더링 설정
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setStyleSheet("background-color: #1a1a2e; border: none;")

        # 이미지 아이템
        self._pixmap_item = QGraphicsPixmapItem()
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self._scene.addItem(self._pixmap_item)

        # 크로스헤어 - 검정 외곽선 + 흰색 대시 (어떤 컬러맵에서도 잘 보임)
        pen_cross_bg = QPen(QColor('#000000'), 3, Qt.PenStyle.SolidLine)
        pen_cross_bg.setCosmetic(True)
        pen_cross_fg = QPen(QColor('#ffffff'), 1, Qt.PenStyle.DashLine)
        pen_cross_fg.setCosmetic(True)

        self._cross_h_bg = QGraphicsLineItem()
        self._cross_v_bg = QGraphicsLineItem()
        self._cross_h_bg.setPen(pen_cross_bg)
        self._cross_v_bg.setPen(pen_cross_bg)
        self._cross_h_bg.setVisible(False)
        self._cross_v_bg.setVisible(False)
        self._scene.addItem(self._cross_h_bg)
        self._scene.addItem(self._cross_v_bg)

        self._cross_h = QGraphicsLineItem()
        self._cross_v = QGraphicsLineItem()
        self._cross_h.setPen(pen_cross_fg)
        self._cross_v.setPen(pen_cross_fg)
        self._cross_h.setVisible(False)
        self._cross_v.setVisible(False)
        self._scene.addItem(self._cross_h)
        self._scene.addItem(self._cross_v)

        # 크로스헤어 색상 (ImageViewer 에서 동기화)
        self._crosshair_color = '#ff0000'

        # 크로스헤어 실시간 좌표 텍스트
        self._cross_text = self._scene.addText("")
        self._cross_text.setDefaultTextColor(QColor('#ff0000'))
        self._cross_text.setVisible(False)
        font = QFont('Segoe UI', 8)
        self._cross_text.setFont(font)

        # 클릭 마커
        self._click_marker = None
        self._click_text = None

        # ROI 드로잉 임시 아이템 (드래그 중 프리뷰)
        self._roi_item = None

        # ROI 매니저 (완성된 ROI 목록)
        self._rois: dict[int, object] = {}   # roi_id -> ROI 객체
        self._selected_roi_id: int | None = None
        self._next_roi_id = 1

        # 활성 ROI 추적 — 패널에 피딩 중인 ROI
        self._active_profile_id: int | None = None
        self._active_hist_id:    int | None = None

        # ROI 콜백 (ImageViewer 에서 설정)
        self.on_roi_added = None
        self.on_roi_selected = None
        self.on_roi_modified = None  # (roi_id) → main_window 에서 프로파일 갱신

        # 상태
        self._img_w = 0
        self._img_h = 0
        self._scale = 1.0
        self._crosshair_on = False
        self._roi_mode = None
        self._draw_start = None
        self._drawing = False
        self._need_fit = False

    # ─────────────────────────────────────────
    # 이미지 설정
    # ─────────────────────────────────────────

    def set_pixmap(self, pixmap: QPixmap, img_w: int, img_h: int, fit: bool = False):
        self._img_w = img_w
        self._img_h = img_h
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(0, 0, img_w, img_h)
        if fit:
            vw = self.viewport().width()
            vh = self.viewport().height()
            if vw > 0 and vh > 0:
                self.fit_to_view()
            else:
                self._need_fit = True  # 뷰포트 준비 후 resizeEvent에서 처리
        self._update_crosshair_size()
        self._emit_scale()

    def fit_to_view(self):
        if self._img_w == 0 or self._img_h == 0:
            return
        vw = self.viewport().width()
        vh = self.viewport().height()
        if vw <= 0 or vh <= 0:
            return
        self._scale = self._calc_fit_scale(vw, vh)
        self._apply_scale()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self._emit_scale()

    def _calc_fit_scale(self, vw: int, vh: int) -> float:
        """뷰포트에 이미지가 딱 맞는 최소 스케일 계산"""
        img_ratio  = self._img_w / self._img_h
        view_ratio = vw / vh
        if img_ratio > view_ratio:
            return vw / self._img_w
        else:
            return vh / self._img_h

    def _apply_scale(self):
        t = QTransform()
        t.scale(self._scale, self._scale)
        self.setTransform(t)

    def _emit_scale(self):
        sb_h = self.horizontalScrollBar()
        sb_v = self.verticalScrollBar()
        self.scale_changed.emit(self._scale, sb_h.value(), sb_v.value())

    def wheelEvent(self, ev: QWheelEvent):
        factor = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15

        old_pos = self.mapToScene(ev.position().toPoint())
        new_scale = self._scale * factor

        # 최소 스케일 = fit 스케일 (이미지가 뷰포트보다 작아지지 않음)
        vw = self.viewport().width()
        vh = self.viewport().height()
        min_scale = self._calc_fit_scale(vw, vh) if vw > 0 and vh > 0 else 0.05
        self._scale = max(min_scale, min(new_scale, 50.0))

        self._apply_scale()
        new_pos = self.mapToScene(ev.position().toPoint())

        delta = new_pos - old_pos
        self.horizontalScrollBar().setValue(
            int(self.horizontalScrollBar().value() - delta.x() * self._scale)
        )
        self.verticalScrollBar().setValue(
            int(self.verticalScrollBar().value() - delta.y() * self._scale)
        )
        self._emit_scale()
        self.update_roi_handle_sizes()
        ev.accept()

    # ─────────────────────────────────────────
    # 마우스 이벤트
    # ─────────────────────────────────────────

    def mouseMoveEvent(self, ev: QMouseEvent):
        scene_pos = self.mapToScene(ev.pos())
        x, y = scene_pos.x(), scene_pos.y()
        self.mouse_moved.emit(x, y)

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(ev.pos())
            x, y = scene_pos.x(), scene_pos.y()

            if self._roi_mode in ('line', 'box', 'histogram'):
                self._drawing = True
                self._draw_start = (x, y)
                self._clear_roi_item()
                ev.accept()
                return  # 씬으로 전파 차단
            else:
                # None 모드: 기존 ROI 클릭 선택 시도 → 없으면 클릭 마커
                hit = self._find_roi_at(x, y)
                if hit is not None:
                    self._select_roi(hit)
                    ev.accept()
                    return
                self.mouse_clicked.emit(x, y)
                self._place_click_marker(x, y)

        super().mousePressEvent(ev)

    def _find_roi_at(self, x: float, y: float):
        """클릭 위치에서 가장 가까운 ROI id 반환 (없으면 None).

        Line: 선분까지 거리 5 screen-px 이내
        Box/Hist: 테두리까지 5 screen-px 이내
        """
        import math
        tol = 6.0 / max(self._scale, 0.01)   # 6 screen-pixels tolerance
        best_id = None
        best_d  = float('inf')
        for roi_id, roi in self._rois.items():
            try:
                pts = roi.pts
                (x0, y0), (x1, y1) = pts[0], pts[1]
            except (IndexError, AttributeError, TypeError):
                continue
            if roi.roi_type == 'Line':
                d = self._dist_point_segment(x, y, x0, y0, x1, y1)
            else:   # Box / Hist — 테두리 근접 판별
                # 외부 박스 안에 있고 내부 박스 밖에 있으면 테두리
                in_outer = (x0 - tol <= x <= x1 + tol) and (y0 - tol <= y <= y1 + tol)
                in_inner = (x0 + tol <  x <  x1 - tol) and (y0 + tol <  y <  y1 - tol)
                d = 0.0 if (in_outer and not in_inner) else float('inf')
            if d < tol and d < best_d:
                best_d  = d
                best_id = roi_id
        return best_id

    @staticmethod
    def _dist_point_segment(px, py, x0, y0, x1, y1) -> float:
        """점 (px,py)과 선분 (x0,y0)-(x1,y1) 사이의 최소 거리."""
        import math
        dx, dy = x1 - x0, y1 - y0
        seg_len2 = dx * dx + dy * dy
        if seg_len2 == 0:
            return math.hypot(px - x0, py - y0)
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / seg_len2))
        return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))

    def mouseMoveEvent(self, ev: QMouseEvent):
        scene_pos = self.mapToScene(ev.pos())
        x, y = scene_pos.x(), scene_pos.y()
        self.mouse_moved.emit(x, y)

        if self._crosshair_on:
            if 0 <= x <= self._img_w and 0 <= y <= self._img_h:
                self._cross_h.setVisible(True)
                self._cross_v.setVisible(True)
                self._cross_h.setLine(0, y, self._img_w, y)
                self._cross_v.setLine(x, 0, x, self._img_h)
                if hasattr(self, '_cross_h_bg'):
                    self._cross_h_bg.setVisible(True)
                    self._cross_v_bg.setVisible(True)
                    self._cross_h_bg.setLine(0, y, self._img_w, y)
                    self._cross_v_bg.setLine(x, 0, x, self._img_h)
                self._cross_text.setVisible(True)
                self._cross_text.setPlainText(f"({int(x)}, {int(y)})")
                ts = 1.0 / max(self._scale, 0.1)
                self._cross_text.setScale(ts)
                offset = 8 / max(self._scale, 0.1)
                self._cross_text.setPos(x + offset, y - offset)
            else:
                self._cross_h.setVisible(False)
                self._cross_v.setVisible(False)
                if hasattr(self, '_cross_h_bg'):
                    self._cross_h_bg.setVisible(False)
                    self._cross_v_bg.setVisible(False)
                self._cross_text.setVisible(False)
        else:
            self._cross_text.setVisible(False)

        if self._drawing and self._draw_start is not None:
            self._update_roi_preview(self._draw_start[0], self._draw_start[1], x, y)
            ev.accept()  # 드로잉 중엔 씬 전파 차단
            return

        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton and self._drawing:
            scene_pos = self.mapToScene(ev.pos())
            x, y = scene_pos.x(), scene_pos.y()
            self._drawing = False
            if self._draw_start is not None:
                self._finalize_roi(self._draw_start[0], self._draw_start[1], x, y)
            self._draw_start = None
            ev.accept()  # 씬 전파 차단
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            # 더블클릭으로 클릭 마커 제거
            if self._click_marker:
                for item in self._click_marker:
                    self._scene.removeItem(item)
                self._click_marker = None
            if self._click_text:
                self._scene.removeItem(self._click_text)
                self._click_text = None
            self.mouse_clicked.emit(-1, -1)  # 마커 제거 알림
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)
        if ev.button() == Qt.MouseButton.LeftButton and self._drawing:
            scene_pos = self.mapToScene(ev.pos())
            x, y = scene_pos.x(), scene_pos.y()
            self._drawing = False
            if self._draw_start is not None:
                self._finalize_roi(self._draw_start[0], self._draw_start[1], x, y)
            self._draw_start = None
        super().mouseReleaseEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_crosshair_size()
        # 리사이즈 시 현재 스케일이 최소 스케일보다 작으면 fit
        if self._img_w > 0 and self._img_h > 0:
            vw = self.viewport().width()
            vh = self.viewport().height()
            if vw > 0 and vh > 0:
                min_scale = self._calc_fit_scale(vw, vh)
                if self._scale < min_scale:
                    self._scale = min_scale
                    self._apply_scale()
        self._emit_scale()
        if hasattr(self, '_need_fit') and self._need_fit and self._img_w > 0:
            self._need_fit = False
            self.fit_to_view()

    def leaveEvent(self, ev):
        """마우스가 뷰어 밖으로 나가면 크로스헤어 숨김"""
        self._cross_h.setVisible(False)
        self._cross_v.setVisible(False)
        if hasattr(self, '_cross_h_bg'):
            self._cross_h_bg.setVisible(False)
            self._cross_v_bg.setVisible(False)
        self._cross_text.setVisible(False)
        super().leaveEvent(ev)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._emit_scale()

    # ─────────────────────────────────────────
    # 크로스헤어
    # ─────────────────────────────────────────

    def set_crosshair(self, on: bool):
        self._crosshair_on = on
        self._cross_h.setVisible(on)
        self._cross_v.setVisible(on)
        if hasattr(self, '_cross_h_bg'):
            self._cross_h_bg.setVisible(on)
            self._cross_v_bg.setVisible(on)

    def set_crosshair_color(self, color: str):
        self._crosshair_color = color
        pen_fg = QPen(QColor(color), 1, Qt.PenStyle.DashLine)
        pen_fg.setCosmetic(True)
        self._cross_h.setPen(pen_fg)
        self._cross_v.setPen(pen_fg)
        # 크로스헤어 텍스트도 같은 색
        self._cross_text.setDefaultTextColor(QColor(color))

    def _update_crosshair_size(self):
        if self._img_w > 0 and self._img_h > 0:
            self._cross_h.setLine(0, 0, self._img_w, 0)
            self._cross_v.setLine(0, 0, 0, self._img_h)
            if hasattr(self, '_cross_h_bg'):
                self._cross_h_bg.setLine(0, 0, self._img_w, 0)
                self._cross_v_bg.setLine(0, 0, 0, self._img_h)

    # ─────────────────────────────────────────
    # 클릭 마커
    # ─────────────────────────────────────────

    def _place_click_marker(self, x: float, y: float):
        # 기존 마커 제거
        if self._click_marker:
            for item in (self._click_marker if isinstance(self._click_marker, list) else [self._click_marker]):
                self._scene.removeItem(item)
        if self._click_text:
            self._scene.removeItem(self._click_text)

        size = 6 / max(self._scale, 0.1)  # scene 좌표 (cosmetic pen 사용)
        color = self._crosshair_color
        pen = QPen(QColor(color), 1)
        pen.setCosmetic(True)

        # + 마커
        h_line = QGraphicsLineItem(x - size, y, x + size, y)
        v_line = QGraphicsLineItem(x, y - size, x, y + size)
        h_line.setPen(pen)
        v_line.setPen(pen)

        self._scene.addItem(h_line)
        self._scene.addItem(v_line)
        self._click_marker = [h_line, v_line]

        # 좌표 텍스트
        text = self._scene.addText(f"({int(x)}, {int(y)})")
        text.setDefaultTextColor(QColor(color))
        font = QFont('Segoe UI', 8)
        text.setFont(font)
        # 텍스트도 화면 픽셀 고정을 위해 역스케일 적용
        text_scale = 1.0 / max(self._scale, 0.1)
        text.setScale(text_scale)
        marker_offset = 6 / max(self._scale, 0.1)
        text.setPos(x + marker_offset, y - marker_offset)
        self._click_text = text

    # ─────────────────────────────────────────
    # ROI
    # ─────────────────────────────────────────

    def set_roi_mode(self, mode):
        self._roi_mode = mode
        self._clear_roi_item()
        if mode in ('line', 'box', 'histogram'):
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _clear_roi_item(self):
        if self._roi_item:
            if isinstance(self._roi_item, list):
                for item in self._roi_item:
                    self._scene.removeItem(item)
            else:
                self._scene.removeItem(self._roi_item)
            self._roi_item = None

    def _update_roi_preview(self, x0, y0, x1, y1):
        self._clear_roi_item()
        pen_dash = QPen(QColor('#e94560'), 1, Qt.PenStyle.DashLine)
        pen_dash.setCosmetic(True)
        pen_hist = QPen(QColor('#4ecdc4'), 1, Qt.PenStyle.DashLine)
        pen_hist.setCosmetic(True)

        if self._roi_mode == 'line':
            item = QGraphicsLineItem(x0, y0, x1, y1)
            item.setPen(pen_dash)
            self._scene.addItem(item)
            self._roi_item = item
        elif self._roi_mode in ('box', 'histogram'):
            pen = pen_hist if self._roi_mode == 'histogram' else pen_dash
            rect = QRectF(min(x0,x1), min(y0,y1), abs(x1-x0), abs(y1-y0))
            item = QGraphicsRectItem(rect)
            item.setPen(pen)
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._scene.addItem(item)
            self._roi_item = item

    def _finalize_roi(self, x0, y0, x1, y1):
        self._clear_roi_item()

        # 최소 크기 체크 (이미지 픽셀 기준)
        import math
        MIN_LINE = 10
        MIN_BOX  = 10

        if self._roi_mode == 'line':
            if math.hypot(x1 - x0, y1 - y0) < MIN_LINE:
                return
        elif self._roi_mode in ('box', 'histogram'):
            if abs(x1 - x0) < MIN_BOX or abs(y1 - y0) < MIN_BOX:
                return

        roi_id = self._next_roi_id
        self._next_roi_id += 1

        if self._roi_mode == 'line':
            roi = LineROI(self._scene, lambda: self._scale, roi_id, x0, y0, x1, y1)
        elif self._roi_mode == 'box':
            roi = BoxROI(self._scene, lambda: self._scale, roi_id, x0, y0, x1, y1)
        elif self._roi_mode == 'histogram':
            roi = HistROI(self._scene, lambda: self._scale, roi_id, x0, y0, x1, y1)
        else:
            return

        roi.modified.connect(lambda: self._on_roi_modified(roi_id))
        self._rois[roi_id] = roi
        self._select_roi(roi_id)

        if self.on_roi_added:
            self.on_roi_added(roi)

        self.roi_drawn.emit(self._roi_mode, roi.pts)  # _roi_mode 는 이미 'line','box','histogram'

    def _select_roi(self, roi_id: Optional[int]):
        # 이전 선택 해제
        if self._selected_roi_id is not None:
            old = self._rois.get(self._selected_roi_id)
            if old:
                old.select(False)
        self._selected_roi_id = roi_id
        if roi_id is not None:
            roi = self._rois.get(roi_id)
            if roi:
                roi.select(True)
        if self.on_roi_selected:
            self.on_roi_selected(roi_id)

    def delete_roi(self, roi_id: int):
        roi = self._rois.pop(roi_id, None)
        if roi:
            roi.remove()
        if self._selected_roi_id == roi_id:
            self._selected_roi_id = None
        if self._active_profile_id == roi_id:
            self._active_profile_id = None
        if self._active_hist_id == roi_id:
            self._active_hist_id = None

    def delete_all_rois(self):
        for roi_id in list(self._rois.keys()):
            self.delete_roi(roi_id)

    # ── Active ROI 강조 ──────────────────────────────────────────────────────

    def set_active_roi(self, roi_id: int, role: str) -> None:
        """
        지정 ROI를 활성 상태로 강조한다.

        role: 'profile' | 'hist'
        같은 role의 이전 active ROI는 먼저 deactivate.
        """
        if role == 'profile':
            # 이전 해제
            if self._active_profile_id is not None and \
               self._active_profile_id != roi_id:
                old = self._rois.get(self._active_profile_id)
                if old:
                    old.set_active_profile(False)
            self._active_profile_id = roi_id
            new = self._rois.get(roi_id)
            if new:
                new.set_active_profile(True)

        elif role == 'hist':
            if self._active_hist_id is not None and \
               self._active_hist_id != roi_id:
                old = self._rois.get(self._active_hist_id)
                if old:
                    old.set_active_hist(False)
            self._active_hist_id = roi_id
            new = self._rois.get(roi_id)
            if new:
                new.set_active_hist(True)

    def clear_active_roi(self, role: str) -> None:
        """지정 role의 active ROI 강조를 해제한다."""
        if role == 'profile' and self._active_profile_id is not None:
            roi = self._rois.get(self._active_profile_id)
            if roi:
                roi.set_active_profile(False)
            self._active_profile_id = None
        elif role == 'hist' and self._active_hist_id is not None:
            roi = self._rois.get(self._active_hist_id)
            if roi:
                roi.set_active_hist(False)
            self._active_hist_id = None

    def get_roi(self, roi_id: int):
        return self._rois.get(roi_id)

    def _on_roi_modified(self, roi_id: int):
        roi = self._rois.get(roi_id)
        if roi:
            type_map = {'Line': 'line', 'Box': 'box', 'Hist': 'histogram'}
            mode = type_map.get(roi.roi_type, roi.roi_type.lower())
            self.roi_drawn.emit(mode, roi.pts)
            if self.on_roi_modified:
                self.on_roi_modified(roi_id)

    def update_roi_handle_sizes(self):
        for roi in self._rois.values():
            roi.update_handle_sizes()


# ─────────────────────────────────────────────────────────────────────────────
# 메인 ImageViewer 위젯
# ─────────────────────────────────────────────────────────────────────────────

class ImageViewer(QWidget):
    def closeEvent(self, event):
        # 진행 중인 worker 시그널 끊기 — finished.connect(deleteLater)가 자율 정리
        if self._colormap_worker is not None:
            try:
                self._colormap_worker.colormap_applied.disconnect()
            except Exception:
                pass
            self._colormap_worker = None
        super().closeEvent(event)
    # 시그널
    line_profile_updated = pyqtSignal(object, str)
    box_profile_updated  = pyqtSignal(object, object, str)
    histogram_updated    = pyqtSignal(object, object)
    pixel_info_updated   = pyqtSignal(int, int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_image: np.ndarray | None = None
        self._current_cmap = 'off'
        self._crosshair_color = '#ff0000'
        self._last_click_info = None  # (ix, iy, val)
        self._roi_line_pts = None
        self._roi_box_pts  = None
        self._roi_hist_pts = None
        self._roi_mode = None
        # 패널에 연결된 ROI id 추적
        self._active_profile_roi_id: int | None = None
        self._active_hist_roi_id:    int | None = None
        self._last_profile_t: float = 0.0   # profile throttle용 타임스탬프
        self._colormap_worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 툴바 ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 4, 4, 4)
        toolbar.setSpacing(6)

        # ROI 콤보
        roi_label = QLabel("ROI:")
        roi_label.setStyleSheet("color: #a0a0b0; font-size: 11px;")
        toolbar.addWidget(roi_label)

        self.roi_combo = QComboBox()
        self.roi_combo.addItems(["None", "Line Profile", "Box Profile",
                                  "X Profile", "Y Profile", "Histogram"])
        self.roi_combo.setFixedWidth(130)
        self.roi_combo.currentTextChanged.connect(self._on_roi_mode_changed)
        toolbar.addWidget(self.roi_combo)

        # 컬러맵
        cmap_label = QLabel("  Colormap:")
        cmap_label.setStyleSheet("color: #a0a0b0; font-size: 11px;")
        toolbar.addWidget(cmap_label)

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["Off", "jet", "viridis", "plasma", "hot", "grey"])
        self.cmap_combo.setFixedWidth(90)
        self.cmap_combo.currentTextChanged.connect(self._on_cmap_changed)
        toolbar.addWidget(self.cmap_combo)

        # 크로스헤어 토글
        self.btn_crosshair = QToolButton()
        self.btn_crosshair.setText("✛ Crosshair")
        self.btn_crosshair.setCheckable(True)
        self.btn_crosshair.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:checked {
                background: #e94560; color: #fff; border-color: #e94560;
            }
            QToolButton:hover { border-color: #e94560; }
        """)
        self.btn_crosshair.toggled.connect(self._on_crosshair_toggled)
        toolbar.addWidget(self.btn_crosshair)

        # 크로스헤어 색상 콤보
        self.crosshair_color_combo = QComboBox()
        self.crosshair_color_combo.setFixedWidth(85)
        self._crosshair_colors = {
            'Red':    '#ff0000',
            'White':  '#ffffff',
            'Yellow': '#ffff00',
            'Cyan':   '#00ffff',
            'Black':  '#000000',
            'Green':  '#00ff00',
        }
        for name in self._crosshair_colors:
            self.crosshair_color_combo.addItem(name)
        self.crosshair_color_combo.currentTextChanged.connect(self._on_crosshair_color_changed)
        toolbar.addWidget(self.crosshair_color_combo)

        # 이미지 내보내기 버튼 (P3-2)
        self.btn_export_img = QToolButton()
        self.btn_export_img.setText("📤")
        self.btn_export_img.setToolTip("처리된 이미지 PNG/TIFF 저장")
        self.btn_export_img.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:hover { border-color: #4ecdc4; color: #4ecdc4; }
        """)
        self.btn_export_img.clicked.connect(self._export_image)
        toolbar.addWidget(self.btn_export_img)

        toolbar.addStretch()

        # 포화 경고 레이블 (P3-3)
        self.lbl_saturated = QLabel("⚠ SATURATED")
        self.lbl_saturated.setStyleSheet(
            "color: #e94560; font-size: 11px; font-weight: bold; "
            "background: #3a0010; border-radius: 3px; padding: 1px 6px;"
        )
        self.lbl_saturated.setVisible(False)
        toolbar.addWidget(self.lbl_saturated)

        # 픽셀 정보
        self.pixel_label = QLabel("Current: -  |  📍 -")
        self.pixel_label.setStyleSheet("color: #e0e0e0; font-size: 11px;")
        toolbar.addWidget(self.pixel_label)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar)
        toolbar_widget.setStyleSheet("background: #16213e;")
        layout.addWidget(toolbar_widget)

        # ── 눈금자 + 뷰어 영역 ──
        viewer_area = QWidget()
        viewer_layout = QVBoxLayout(viewer_area)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(0)

        # 상단 행: 코너 + X 눈금자
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)

        self._corner = QWidget()
        self._corner.setFixedSize(48, 24)
        self._corner.setStyleSheet("background: #16213e;")
        top_row.addWidget(self._corner)

        self._ruler_x = RulerWidget('horizontal')
        top_row.addWidget(self._ruler_x)
        viewer_layout.addLayout(top_row)

        # 하단 행: Y 눈금자 + 뷰어
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(0)

        self._ruler_y = RulerWidget('vertical')
        bottom_row.addWidget(self._ruler_y)

        self._view = ImageGraphicsView()
        self._view.mouse_moved.connect(self._on_mouse_moved)
        self._view.mouse_clicked.connect(self._on_mouse_clicked)
        self._view.roi_drawn.connect(self._on_roi_drawn)
        self._view.scale_changed.connect(self._on_scale_changed)
        # ROI 선택 콜백 — 선택된 ROI 타입에 맞는 패널 갱신
        self._view.on_roi_selected = self._on_roi_selected
        self._view.horizontalScrollBar().valueChanged.connect(
            lambda _: self._on_scale_changed(
                self._view._scale,
                self._view.horizontalScrollBar().value(),
                self._view.verticalScrollBar().value()
            )
        )
        self._view.verticalScrollBar().valueChanged.connect(
            lambda _: self._on_scale_changed(
                self._view._scale,
                self._view.horizontalScrollBar().value(),
                self._view.verticalScrollBar().value()
            )
        )
        bottom_row.addWidget(self._view)
        viewer_layout.addLayout(bottom_row)

        layout.addWidget(viewer_area)

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        """프레임 전환 (뷰 유지)"""
        self._current_image = image
        self._refresh_pixmap(fit=False)
        self._recompute_profile()

    def set_image_first(self, image: np.ndarray):
        """첫 로드 (뷰 fit)"""
        self._current_image = image
        self._refresh_pixmap(fit=True)

    def set_live_frame(self, rgb: np.ndarray, fit: bool = False):
        """라이브 스트리밍 전용 — ColorMapWorker 우회, GUI 스레드에서 직접 변환.
        rgb: uint8 H×W×3 (또는 H×W grayscale)
        프로파일/히스토그램은 최대 10fps로 throttle — scipy/numpy 과부하 방지.
        """
        import time
        from PyQt6.QtGui import QImage, QPixmap
        self._current_image = rgb
        h, w = rgb.shape[:2]
        if rgb.ndim == 3 and rgb.shape[2] == 3:
            qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
        else:
            qimg = QImage(rgb.data, w, h, w, QImage.Format.Format_Grayscale8)
        self._view.set_pixmap(QPixmap.fromImage(qimg.copy()), w, h, fit=fit)
        # ROI 프로파일/히스토그램: 최대 10fps (0.1초 간격)
        now = time.monotonic()
        if now - self._last_profile_t >= 0.1:
            self._last_profile_t = now
            self._recompute_profile()

    def set_source_image(self, img: np.ndarray) -> None:
        """라이브 모드에서 colormap/export 기준이 될 원본 grayscale 이미지를 갱신.
        set_live_frame은 display용 RGB를 받으므로, 원본은 별도로 저장해야
        _refresh_pixmap/_export_image에서 이중 colormap 적용이 발생하지 않는다."""
        self._current_image = img

    def set_saturated(self, saturated: bool, sat_ratio: float = 0.0) -> None:
        """포화 경고 오버레이 표시/숨김 (P3-3)."""
        if saturated:
            self.lbl_saturated.setText(f"⚠ SAT {sat_ratio*100:.2f}%")
            self.lbl_saturated.setVisible(True)
        else:
            self.lbl_saturated.setVisible(False)

    def _export_image(self) -> None:
        """현재 display 이미지를 PNG/TIFF로 저장 (P3-2)."""
        if self._current_image is None:
            return
        from PyQt6.QtWidgets import QFileDialog
        from datetime import datetime
        default = f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "이미지 저장", default,
            "PNG (*.png);;TIFF (*.tif *.tiff);;모든 파일 (*)"
        )
        if not path:
            return
        try:
            img = self._current_image
            cmap = self._current_cmap
            if cmap and cmap != 'off':
                rgba = apply_colormap(img, cmap)
            else:
                from core.async_worker import ColorMapWorker
                rgba = ColorMapWorker._to_grayscale_rgba(img)
            pixmap = ndarray_to_qpixmap(rgba)
            pixmap.save(path)
        except Exception as e:
            print(f"[ImageViewer] 이미지 저장 오류: {e}")

    def _refresh_pixmap(self, fit: bool = False):
        if self._current_image is None:
            return
        # 기존 워커가 있으면 결과 시그널만 끊음.
        # 종료/해제는 각 워커가 finished → deleteLater 로 자율 처리.
        if self._colormap_worker is not None:
            try:
                self._colormap_worker.colormap_applied.disconnect()
            except Exception:
                pass

        img = self._current_image
        cmap = self._current_cmap

        from core.async_worker import ColorMapWorker
        worker = ColorMapWorker(img, cmap)
        self._colormap_worker = worker
        worker.finished.connect(worker.deleteLater)

        def on_colormap_ready(rgba):
            try:
                pixmap = ndarray_to_qpixmap(rgba)
                h, w = img.shape[:2]
                self._view.set_pixmap(pixmap, w, h, fit=fit)
            except Exception as e:
                print(f"[ImageViewer] QPixmap 변환 오류: {e}")
            finally:
                if self._colormap_worker is worker:
                    self._colormap_worker = None

        worker.colormap_applied.connect(on_colormap_ready)
        worker.start()

    # ─────────────────────────────────────────
    # 눈금자 업데이트
    # ─────────────────────────────────────────

    def _on_scale_changed(self, scale: float, x_offset: float, y_offset: float):
        if self._current_image is None:
            return
        h, w = self._current_image.shape[:2]
        # scrollBar.value() 는 scene좌표*scale 단위 → 이미지 픽셀로 변환
        x_img_offset = x_offset / max(scale, 0.001)
        y_img_offset = y_offset / max(scale, 0.001)
        self._ruler_x.update_transform(scale, x_img_offset, w)
        self._ruler_y.update_transform(scale, y_img_offset, h)

    # ─────────────────────────────────────────
    # ROI 선택 → 패널 갱신
    # ─────────────────────────────────────────

    def _on_roi_selected(self, roi_id):
        """클릭 또는 신규 드로우로 ROI가 선택됐을 때 해당 타입 패널을 갱신한다.

        - Line/Box ROI → Profile 패널 (pts 저장하여 재계산에도 사용)
        - Hist ROI     → Histogram 패널 (독립 유지)
        드로우 모드(콤보 선택)와 완전히 분리된다.
        """
        if roi_id is None:
            return
        roi = self._view.get_roi(roi_id)
        if roi is None or self._current_image is None:
            return
        pts = roi.pts
        roi_type = roi.roi_type   # 'Line' | 'Box' | 'Hist'
        try:
            (x0, y0), (x1, y1) = pts[0], pts[1]
        except (IndexError, TypeError):
            return
        if roi_type == 'Line':
            self._roi_line_pts = pts
            self._roi_box_pts  = None
            self._set_active_roi(roi_id, 'profile')
            self._compute_line_profile(x0, y0, x1, y1)
        elif roi_type == 'Box':
            self._roi_box_pts  = pts
            self._roi_line_pts = None
            self._set_active_roi(roi_id, 'profile')
            self._compute_box_profile(x0, y0, x1, y1)
        elif roi_type == 'Hist':
            self._roi_hist_pts = pts
            self._set_active_roi(roi_id, 'hist')
            self._compute_histogram(x0, y0, x1, y1)

    # ─────────────────────────────────────────
    # ROI 모드 (드로우 타입 선택)
    # ─────────────────────────────────────────

    def _on_roi_mode_changed(self, mode_text: str):
        mode_map = {
            "Line Profile": 'line',
            "Box Profile":  'box',
            "X Profile":    'xprofile',
            "Y Profile":    'yprofile',
            "Histogram":    'histogram',
        }
        self._roi_mode = mode_map.get(mode_text, None)
        # ★ pts 초기화 제거 — 드로우 모드만 변경, 기존 출력 패널 유지
        self._view.set_roi_mode(self._roi_mode)

        # X/Y 프로파일: ROI 없이 전체 이미지로 즉시 계산
        if self._roi_mode == 'xprofile':
            self._compute_x_profile()
        elif self._roi_mode == 'yprofile':
            self._compute_y_profile()

    def _on_roi_drawn(self, mode: str, pts: list):
        if mode == 'line':
            self._roi_line_pts = pts
            (x0,y0),(x1,y1) = pts
            self._compute_line_profile(x0, y0, x1, y1)
        elif mode == 'box':
            self._roi_box_pts = pts
            (x0,y0),(x1,y1) = pts
            self._compute_box_profile(x0, y0, x1, y1)
        elif mode == 'histogram':
            self._roi_hist_pts = pts
            (x0,y0),(x1,y1) = pts
            self._compute_histogram(x0, y0, x1, y1)

    def _set_active_roi(self, roi_id: Optional[int], role: str) -> None:
        """
        지정 role의 active ROI를 업데이트하고 강조 시각을 갱신한다.

        role: 'profile' | 'hist'
        roi_id: None 이면 해당 role의 active 해제.
        외부(analysis_tab 등)에서도 직접 호출 가능.
        """
        if roi_id is None:
            self._view.clear_active_roi(role)
            if role == 'profile':
                self._active_profile_roi_id = None
            else:
                self._active_hist_roi_id = None
            return

        self._view.set_active_roi(roi_id, role)
        if role == 'profile':
            self._active_profile_roi_id = roi_id
        else:
            self._active_hist_roi_id = roi_id

    def _recompute_profile(self):
        """프레임 갱신 시 프로파일·히스토그램 독립 재계산.

        드로우 모드와 무관하게 마지막으로 선택된 ROI 기준으로 표시한다.
        프로파일(line/box)과 히스토그램은 서로 독립적으로 유지된다.
        """
        if self._current_image is None:
            return
        # ── 프로파일 패널: 마지막 선택된 프로파일 ROI 기준 ──
        if self._roi_line_pts:
            (x0, y0), (x1, y1) = self._roi_line_pts
            self._compute_line_profile(x0, y0, x1, y1)
        elif self._roi_box_pts:
            (x0, y0), (x1, y1) = self._roi_box_pts
            self._compute_box_profile(x0, y0, x1, y1)
        elif self._roi_mode == 'xprofile':
            self._compute_x_profile()
        elif self._roi_mode == 'yprofile':
            self._compute_y_profile()
        # ── 히스토그램 패널: 마지막 선택된 히스토그램 ROI 기준 (독립) ──
        if self._roi_hist_pts:
            (x0, y0), (x1, y1) = self._roi_hist_pts
            self._compute_histogram(x0, y0, x1, y1)

    # ─────────────────────────────────────────
    # 프로파일 계산
    # ─────────────────────────────────────────

    def _compute_line_profile(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            from scipy import ndimage
            img = self._current_image.astype(np.float64)
            h, w = img.shape[:2]
            num = max(int(np.hypot(x1-x0, y1-y0)), 2)
            xs = np.linspace(np.clip(x0, 0, w-1), np.clip(x1, 0, w-1), num)
            ys = np.linspace(np.clip(y0, 0, h-1), np.clip(y1, 0, h-1), num)
            profile = ndimage.map_coordinates(img, [ys, xs], order=1)
            self.line_profile_updated.emit(profile, "Line")
        except Exception as e:
            print(f"Line profile error: {e}")

    def _compute_box_profile(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            img = self._current_image
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].astype(np.float64)
            self.box_profile_updated.emit(region.mean(axis=0), region.mean(axis=1), "Box")
        except Exception as e:
            print(f"Box profile error: {e}")

    def _compute_histogram(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            img = self._current_image
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].flatten().astype(np.float64)
            num_bins = min(256, int(np.sqrt(len(region))) + 1)
            counts, bin_edges = np.histogram(region, bins=num_bins)
            self.histogram_updated.emit(counts, bin_edges)
        except Exception as e:
            print(f"Histogram error: {e}")

    def _compute_x_profile(self):
        if self._current_image is None:
            return
        self.line_profile_updated.emit(self._current_image.mean(axis=0), "X Profile")

    def _compute_y_profile(self):
        if self._current_image is None:
            return
        self.line_profile_updated.emit(self._current_image.mean(axis=1), "Y Profile")

    def _compute_line_profile_direct(self, frame: np.ndarray, x0, y0, x1, y1):
        """외부에서 직접 프레임 지정해서 라인 프로파일 계산"""
        try:
            from scipy import ndimage
            img = frame.astype(np.float64)
            h, w = img.shape[:2]
            num = max(int(np.hypot(x1-x0, y1-y0)), 2)
            xs = np.linspace(np.clip(x0, 0, w-1), np.clip(x1, 0, w-1), num)
            ys = np.linspace(np.clip(y0, 0, h-1), np.clip(y1, 0, h-1), num)
            profile = ndimage.map_coordinates(img, [ys, xs], order=1)
            self.line_profile_updated.emit(profile, "Line")
        except Exception as e:
            print(f"Line profile error: {e}")

    def _compute_box_profile_direct(self, frame: np.ndarray, x0, y0, x1, y1):
        try:
            img = frame
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].astype(np.float64)
            self.box_profile_updated.emit(region.mean(axis=0), region.mean(axis=1), "Box")
        except Exception as e:
            print(f"Box profile error: {e}")

    def _compute_histogram_direct(self, frame: np.ndarray, x0, y0, x1, y1):
        try:
            img = frame
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0,x1), 0, w-1))
            ix1 = int(np.clip(max(x0,x1), 0, w-1))
            iy0 = int(np.clip(min(y0,y1), 0, h-1))
            iy1 = int(np.clip(max(y0,y1), 0, h-1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].flatten().astype(np.float64)
            num_bins = min(256, int(np.sqrt(len(region))) + 1)
            counts, bin_edges = np.histogram(region, bins=num_bins)
            self.histogram_updated.emit(counts, bin_edges)
        except Exception as e:
            print(f"Histogram error: {e}")

    # ─────────────────────────────────────────
    # 크로스헤어 / 컬러맵
    # ─────────────────────────────────────────

    def _on_crosshair_toggled(self, on: bool):
        self._view.set_crosshair(on)

    def _on_crosshair_color_changed(self, name: str):
        color = self._crosshair_colors.get(name, '#ff0000')
        self._crosshair_color = color
        self._view.set_crosshair_color(color)


    def _on_cmap_changed(self, name: str):
    # 'Off'는 내부적으로 'off'로 처리
        self._current_cmap = name.lower() if name.lower() == 'off' else name
        self._refresh_pixmap(fit=False)

    # ─────────────────────────────────────────
    # 마우스 정보
    # ─────────────────────────────────────────

    def _on_mouse_moved(self, x: float, y: float):
        if self._current_image is None:
            return
        ix, iy = int(x), int(y)
        h, w = self._current_image.shape[:2]
        if 0 <= ix < w and 0 <= iy < h:
            val = self._current_image[iy, ix]
            import numpy as np
            # 칼라(RGB) 픽셀 처리
            if isinstance(val, (np.ndarray, list, tuple)) and len(val) == 3:
                r, g, b = val
                # RGB를 문자열로 emit (시그널 시그니처가 float 하나라면)
                self.pixel_info_updated.emit(ix, iy, float(r))  # R값 emit (필요시 시그널/슬롯 수정)
                # pixel_label에 RGB 모두 표시
                self._update_pixel_label(ix, iy, f"R:{r} G:{g} B:{b}")
            else:
                self.pixel_info_updated.emit(ix, iy, float(val))
                self._update_pixel_label(ix, iy, val)

    def _update_pixel_label(self, ix: int, iy: int, val):
        # val이 RGB 문자열이면 그대로, 아니면 float 포맷
        if isinstance(val, str) and val.startswith("R:"):
            cur = f"Current: X:{ix}  Y:{iy}  {val}"
        else:
            cur = f"Current: X:{ix}  Y:{iy}  Val:{val:.1f}"
        if self._last_click_info is not None:
            lx, ly, lv = self._last_click_info
            # 클릭값도 RGB 문자열 지원
            if isinstance(lv, str) and lv.startswith("R:"):
                last = f"📍 X:{lx}  Y:{ly}  {lv}"
            else:
                last = f"📍 X:{lx}  Y:{ly}  Val:{lv:.1f}"
        else:
            last = "📍 -"
        self.pixel_label.setText(f"{cur}   |   {last}")


    def _on_mouse_clicked(self, x: float, y: float):
        if x < 0 or y < 0:
            # 더블클릭 마커 제거 → 라스트 클릭 정보 초기화
            self._last_click_info = None
            return
        if self._current_image is None:
            return
        ix, iy = int(x), int(y)
        h, w = self._current_image.shape[:2]
        if 0 <= ix < w and 0 <= iy < h:
            val = self._current_image[iy, ix]
            import numpy as np
            # 칼라(RGB) 픽셀 처리: 클릭 시에도 문자열로 저장
            if isinstance(val, (np.ndarray, list, tuple)) and len(val) == 3:
                r, g, b = val
                lv = f"R:{r} G:{g} B:{b}"
            else:
                lv = val
            self._last_click_info = (ix, iy, lv)

    # ─────────────────────────────────────────
    # 하위 호환 (main_window 에서 사용하는 속성들)
    # ─────────────────────────────────────────

    def autoRange(self):
        self._view.fit_to_view()

    @property
    def image_view(self):
        """하위 호환용 - main_window 에서 image_viewer.image_view.autoRange() 호출"""
        return self