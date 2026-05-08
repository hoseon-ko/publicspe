"""ui/viewer/graphics_view.py — ImageGraphicsView + _RangePopup."""
from __future__ import annotations

import numpy as np
from ui.roi_items import LineROI, BoxROI, HistROI, HandleItem
from ui.colormap_utils import apply_colormap, ndarray_to_qpixmap
from ui.histogram_range_widget import HistogramRangeWidget
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsLineItem, QGraphicsRectItem,
    QSizePolicy, QToolButton, QPushButton,
    QListWidget, QListWidgetItem, QApplication, QMenu,
)
from PyQt6.QtCore import pyqtSignal, Qt, QRectF, QTimer
from PyQt6.QtGui import (
    QPixmap, QImage, QPen, QColor, QBrush, QPainter,
    QWheelEvent, QMouseEvent, QFont, QTransform
)
from typing import Optional, Union


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
    sel_box_changed  = pyqtSignal(float, float, float, float)  # x0,y0,x1,y1 (이미지 좌표); 음수=-해제

    _SEL_HN = ['NW', 'N', 'NE', 'E', 'SE', 'S', 'SW', 'W']

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
        self._click_crosshair = None   # 고정 크로스헤어 (클릭 시)

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

        # 선택 박스 (None 모드 드래그)
        self._sel_active = False
        self._sel_x0 = self._sel_y0 = self._sel_x1 = self._sel_y1 = 0.0
        self._sel_drag_mode: str | None = None   # None/'create'/'move'/'resize-NW' 등
        self._sel_press_x = self._sel_press_y = 0.0
        self._sel_save = (0.0, 0.0, 0.0, 0.0)   # (x0,y0,x1,y1) at press
        self._sel_rect_gfx: 'QGraphicsRectItem | None' = None
        self._sel_hdl_gfx: list = []
        self._sel_create_visual()

        # 단일 ROI 모드 (새 ROI 생성 시 기존 것 모두 삭제)
        self._single_roi_mode = False

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

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.RightButton:
            # 우클릭: 마커든 박스든 제거 → 전체 평균 프로파일
            cleared = False
            if self._click_marker or self._click_crosshair:
                self._clear_click_marker()
                self.mouse_clicked.emit(-1, -1)
                cleared = True
            if self._sel_active:
                self._sel_hide()
                self.sel_box_changed.emit(-1.0, -1.0, -1.0, -1.0)
                cleared = True
            if cleared:
                ev.accept()
                return

        if ev.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(ev.pos())

            # 핸들 클릭이면 씬으로 전파 — 드로잉 모드와 무관하게 ROI 편집 허용
            for item in self._scene.items(scene_pos):
                if isinstance(item, HandleItem) and item.isVisible():
                    super().mousePressEvent(ev)
                    return

            x, y = scene_pos.x(), scene_pos.y()

            if self._roi_mode in ('line', 'box', 'histogram'):
                self._drawing = True
                self._draw_start = (x, y)
                self._clear_roi_item()
                ev.accept()
                return
            else:
                # None 모드: 선택 박스 > 기존 ROI > 새 드래그
                hit_sel = self._sel_hit(x, y)

                if hit_sel == 'inside':
                    # 박스 내부 → 이동
                    self._sel_drag_mode = 'move'
                    self._sel_press_x, self._sel_press_y = x, y
                    self._sel_save = self._sel_norm()
                    ev.accept()
                    return

                if hit_sel is not None:
                    # 핸들 → 리사이즈
                    self._sel_drag_mode = f'resize-{hit_sel}'
                    self._sel_press_x, self._sel_press_y = x, y
                    self._sel_save = self._sel_norm()
                    ev.accept()
                    return

                # 기존 ROI 선택 시도
                hit = self._find_roi_at(x, y)
                if hit is not None:
                    self._select_roi(hit)
                    ev.accept()
                    return

                # 드래그/클릭 시작 — 마커/박스는 릴리즈 때 확정
                self._sel_drag_mode = 'create'
                self._sel_press_x, self._sel_press_y = x, y

        super().mousePressEvent(ev)

    # ─────────────────────────────────────────
    # 선택 박스 (None 모드 드래그 분석 영역)
    # ─────────────────────────────────────────

    def _sel_create_visual(self):
        from PyQt6.QtWidgets import QGraphicsRectItem as _GRI
        from PyQt6.QtCore import QRectF as _RF
        pen = QPen(QColor('#ff3333'), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._sel_rect_gfx = _GRI()
        self._sel_rect_gfx.setPen(pen)
        self._sel_rect_gfx.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._sel_rect_gfx.setVisible(False)
        self._scene.addItem(self._sel_rect_gfx)

        from PyQt6.QtWidgets import QGraphicsItem
        hdl_pen   = QPen(QColor('#ffffff'), 1)
        hdl_brush = QBrush(QColor('#ffffff'))
        for _ in self._SEL_HN:
            h = _GRI(-4, -4, 8, 8)
            h.setPen(hdl_pen)
            h.setBrush(hdl_brush)
            h.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            h.setVisible(False)
            self._scene.addItem(h)
            self._sel_hdl_gfx.append(h)

    def _sel_norm(self):
        return (min(self._sel_x0, self._sel_x1), min(self._sel_y0, self._sel_y1),
                max(self._sel_x0, self._sel_x1), max(self._sel_y0, self._sel_y1))

    def _sel_update_visual(self):
        from PyQt6.QtCore import QRectF as _RF
        x0, y0, x1, y1 = self._sel_norm()
        self._sel_rect_gfx.setRect(_RF(x0, y0, x1 - x0, y1 - y0))
        self._sel_rect_gfx.setVisible(True)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        positions = [(x0,y0),(mx,y0),(x1,y0),(x1,my),(x1,y1),(mx,y1),(x0,y1),(x0,my)]
        for h, (hx, hy) in zip(self._sel_hdl_gfx, positions):
            h.setPos(hx, hy)
            h.setVisible(True)

    def _sel_hide(self):
        if self._sel_rect_gfx:
            self._sel_rect_gfx.setVisible(False)
        for h in self._sel_hdl_gfx:
            h.setVisible(False)
        self._sel_active = False

    def _sel_hit(self, x: float, y: float) -> str | None:
        """'NW'등 핸들명, 'inside', 또는 None 반환."""
        if not self._sel_active:
            return None
        tol = 7.0 / max(self._scale, 0.01)
        x0, y0, x1, y1 = self._sel_norm()
        mx, my = (x0+x1)/2, (y0+y1)/2
        positions = {
            'NW':(x0,y0),'N':(mx,y0),'NE':(x1,y0),'E':(x1,my),
            'SE':(x1,y1),'S':(mx,y1),'SW':(x0,y1),'W':(x0,my)
        }
        for name, (hx, hy) in positions.items():
            if abs(x - hx) <= tol and abs(y - hy) <= tol:
                return name
        if x0 <= x <= x1 and y0 <= y <= y1:
            return 'inside'
        return None

    _SEL_MIN_PX = 5   # 박스 최소 크기 (이미지 픽셀)

    def _sel_emit(self):
        x0, y0, x1, y1 = self._sel_norm()
        if (x1 - x0) < self._SEL_MIN_PX or (y1 - y0) < self._SEL_MIN_PX:
            return
        self.sel_box_changed.emit(x0, y0, x1, y1)

    def _sel_apply_resize(self, handle: str, dx: float, dy: float):
        x0, y0, x1, y1 = self._sel_save
        if 'W' in handle: x0 += dx
        if 'E' in handle: x1 += dx
        if 'N' in handle: y0 += dy
        if 'S' in handle: y1 += dy
        self._sel_x0, self._sel_y0 = x0, y0
        self._sel_x1, self._sel_y1 = x1, y1

    def _sel_cursor(self, hit: str | None):
        _map = {
            'inside': Qt.CursorShape.SizeAllCursor,
            'NW': Qt.CursorShape.SizeFDiagCursor, 'SE': Qt.CursorShape.SizeFDiagCursor,
            'NE': Qt.CursorShape.SizeBDiagCursor, 'SW': Qt.CursorShape.SizeBDiagCursor,
            'N':  Qt.CursorShape.SizeVerCursor,   'S':  Qt.CursorShape.SizeVerCursor,
            'E':  Qt.CursorShape.SizeHorCursor,   'W':  Qt.CursorShape.SizeHorCursor,
        }
        if hit in _map:
            self.viewport().setCursor(_map[hit])
        else:
            self.viewport().unsetCursor()

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
    def _constrain_angle(x0, y0, x1, y1):
        """Shift 키 — 45° 배수로 스냅 (line 전용)."""
        import math
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length == 0:
            return x1, y1
        angle   = math.atan2(dy, dx)
        snapped = round(angle / (math.pi / 4)) * (math.pi / 4)
        return x0 + length * math.cos(snapped), y0 + length * math.sin(snapped)

    @staticmethod
    def _constrain_square(x0, y0, x1, y1):
        """Shift 키 — box/histogram 을 정사각형으로 제한."""
        import math
        dx, dy = x1 - x0, y1 - y0
        size = max(abs(dx), abs(dy))
        return x0 + math.copysign(size, dx), y0 + math.copysign(size, dy)

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

        # 선택 박스 드래그 처리
        if self._sel_drag_mode is not None:
            dx = x - self._sel_press_x
            dy = y - self._sel_press_y
            if self._sel_drag_mode == 'create':
                if abs(dx) > self._SEL_MIN_PX or abs(dy) > self._SEL_MIN_PX:
                    # 드래그 확정 → 마커 제거, 박스 표시
                    if self._click_marker or self._click_crosshair:
                        self._clear_click_marker()
                        self.mouse_clicked.emit(-1, -1)
                    self._sel_x0, self._sel_y0 = self._sel_press_x, self._sel_press_y
                    self._sel_x1, self._sel_y1 = x, y
                    self._sel_active = True
                    self._sel_update_visual()
                    self._sel_emit()
            elif self._sel_drag_mode == 'move':
                sx0, sy0, sx1, sy1 = self._sel_save
                self._sel_x0, self._sel_y0 = sx0 + dx, sy0 + dy
                self._sel_x1, self._sel_y1 = sx1 + dx, sy1 + dy
                self._sel_update_visual()
                self._sel_emit()
            elif self._sel_drag_mode.startswith('resize-'):
                self._sel_apply_resize(self._sel_drag_mode[7:], dx, dy)
                self._sel_update_visual()
                self._sel_emit()
            ev.accept()
            return

        # 커서 힌트 (드래그 없을 때)
        if self._roi_mode is None:
            self._sel_cursor(self._sel_hit(x, y))

        if self._drawing and self._draw_start is not None:
            x1, y1 = x, y
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                x0, y0 = self._draw_start
                if self._roi_mode == 'line':
                    x1, y1 = self._constrain_angle(x0, y0, x1, y1)
                elif self._roi_mode in ('box', 'histogram'):
                    x1, y1 = self._constrain_square(x0, y0, x1, y1)
            self._update_roi_preview(self._draw_start[0], self._draw_start[1], x1, y1)
            ev.accept()  # 드로잉 중엔 씬 전파 차단
            return

        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton and self._sel_drag_mode is not None:
            scene_pos = self.mapToScene(ev.pos())
            x, y = scene_pos.x(), scene_pos.y()
            mode = self._sel_drag_mode
            self._sel_drag_mode = None

            if mode == 'create':
                dx = x - self._sel_press_x
                dy = y - self._sel_press_y
                if abs(dx) > self._SEL_MIN_PX or abs(dy) > self._SEL_MIN_PX:
                    # 드래그 → 박스 확정, 마커 없음
                    self._sel_x1, self._sel_y1 = x, y
                    self._sel_active = True
                    self._sel_update_visual()
                    self._sel_emit()
                else:
                    # 클릭 → 마커 생성, 기존 박스 해제
                    if self._sel_active:
                        self._sel_hide()
                        self.sel_box_changed.emit(-1.0, -1.0, -1.0, -1.0)
                    self.mouse_clicked.emit(self._sel_press_x, self._sel_press_y)
                    self._place_click_marker(self._sel_press_x, self._sel_press_y)
            elif self._sel_active:
                self._sel_emit()

            ev.accept()
            return

        if ev.button() == Qt.MouseButton.LeftButton and self._drawing:
            scene_pos = self.mapToScene(ev.pos())
            x, y = scene_pos.x(), scene_pos.y()
            self._drawing = False
            if self._draw_start is not None:
                x0, y0 = self._draw_start
                x1, y1 = x, y
                if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if self._roi_mode == 'line':
                        x1, y1 = self._constrain_angle(x0, y0, x1, y1)
                    elif self._roi_mode in ('box', 'histogram'):
                        x1, y1 = self._constrain_square(x0, y0, x1, y1)
                self._finalize_roi(x0, y0, x1, y1)
            self._draw_start = None
            ev.accept()  # 씬 전파 차단
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._clear_click_marker()
            self.mouse_clicked.emit(-1, -1)
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

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
        """마우스가 뷰어 밖으로 나가면 크로스헤어 숨김 + 드래그 상태 초기화"""
        # 탭 전환 등으로 마우스 이탈 시 드래그 상태 잔류 방지
        if self._sel_drag_mode is not None:
            self._sel_drag_mode = None
            self.viewport().unsetCursor()
        if self._drawing:
            self._drawing = False
            self._draw_start = None
            self._clear_roi_item()
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

    def _clear_click_marker(self):
        """클릭 마커 + 고정 크로스헤어 모두 제거."""
        if self._click_marker:
            for item in (self._click_marker if isinstance(self._click_marker, list) else [self._click_marker]):
                self._scene.removeItem(item)
            self._click_marker = None
        if self._click_text:
            self._scene.removeItem(self._click_text)
            self._click_text = None
        if self._click_crosshair:
            for item in self._click_crosshair:
                self._scene.removeItem(item)
            self._click_crosshair = None

    def _place_click_marker(self, x: float, y: float):
        self._clear_click_marker()

        color = self._crosshair_color

        # 전체 이미지에 걸친 고정 크로스헤어
        ch_pen = QPen(QColor(color), 1, Qt.PenStyle.DashLine)
        ch_pen.setCosmetic(True)
        ch_h = QGraphicsLineItem(0, y, self._img_w, y)
        ch_v = QGraphicsLineItem(x, 0, x, self._img_h)
        ch_h.setPen(ch_pen)
        ch_v.setPen(ch_pen)
        ch_h.setOpacity(0.6)
        ch_v.setOpacity(0.6)
        self._scene.addItem(ch_h)
        self._scene.addItem(ch_v)
        self._click_crosshair = [ch_h, ch_v]

        # + 중심 마커
        size = 6 / max(self._scale, 0.1)
        pen = QPen(QColor(color), 2)
        pen.setCosmetic(True)
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
        text.setFont(QFont('Segoe UI', 8))
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

    def set_single_roi_mode(self, enabled: bool):
        self._single_roi_mode = enabled

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

        if self._single_roi_mode:
            self.delete_all_rois()

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
# Range 슬라이더 플로팅 팝업
# ─────────────────────────────────────────────────────────────────────────────

class _RangePopup(QWidget):
    """히스토그램 + 듀얼 핸들 Range 슬라이더를 담는 드래그 가능 플로팅 팝업."""

    closed = pyqtSignal()

    def __init__(self, hist_widget: 'HistogramRangeWidget', parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumSize(480, 156)
        self._drag_pos = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        # ── 타이틀 바 ──
        self._title_bar = QWidget()
        self._title_bar.setFixedHeight(26)
        self._title_bar.setStyleSheet(
            "background:#0d1a2e; border-bottom:1px solid #1a3a60;"
        )
        tb_row = QHBoxLayout(self._title_bar)
        tb_row.setContentsMargins(10, 0, 4, 0)
        lbl = QLabel("📊  COLOR RANGE")
        lbl.setStyleSheet(
            "color:#4a6a8a; font-family:'Courier New'; font-size:13px;"
            " font-weight:bold; letter-spacing:2px; background:transparent;"
        )
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setStyleSheet(
            "QPushButton{background:transparent;color:#506080;border:none;font-size:16px;}"
            "QPushButton:hover{color:#e94560;}"
        )
        tb_row.addWidget(lbl, 1)
        tb_row.addWidget(btn_close)
        outer.addWidget(self._title_bar)

        # ── 히스토그램 위젯 ──
        outer.addWidget(hist_widget, 1)

        self.setObjectName("rangePopup")
        self.setStyleSheet(
            "#rangePopup{background:#0a1020; border:1px solid #1a3a60; border-radius:4px;}"
        )

        btn_close.clicked.connect(self._on_close)
        self._title_bar.mousePressEvent   = self._tb_press
        self._title_bar.mouseMoveEvent    = self._tb_move
        self._title_bar.mouseReleaseEvent = lambda e: setattr(self, '_drag_pos', None)

    def _on_close(self):
        self.hide()
        self.closed.emit()

    def _tb_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_move(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
