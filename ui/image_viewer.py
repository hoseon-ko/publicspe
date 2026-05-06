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
from __future__ import annotations

import numpy as np
from ui.roi_items import LineROI, BoxROI, HistROI, HandleItem
from ui.colormap_utils import apply_colormap, ndarray_to_qpixmap  # re-export for backward compat
from ui.histogram_range_widget import HistogramRangeWidget
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsLineItem, QGraphicsRectItem,
    QSizePolicy, QToolButton, QPushButton,
    QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import pyqtSignal, Qt, QRectF, QTimer
from PyQt6.QtGui import (
    QPixmap, QImage, QPen, QColor, QBrush, QPainter,
    QWheelEvent, QMouseEvent, QFont, QTransform
)

from typing import Optional, Union


# ─────────────────────────────────────────────────────────────────────────────
# 눈금자 위젯
# ─────────────────────────────────────────────────────────────────────────────

class RulerWidget(QWidget):
    """픽셀 단위 눈금자 + 미니 프로파일 오버레이.

    수평 룰러: 상단 _prof_size px = 프로파일, 하단 _TICK px = 눈금
    수직 룰러: 좌측 _prof_size px = 프로파일, 우측 _TICK px = 눈금
    구분선 드래그로 프로파일 영역 크기 조절 가능.
    """

    _TICK    = 24   # 눈금 영역 고정 크기 (px)
    _SEP_HIT = 5    # 구분선 감지 반경 (px)
    _PROF_MIN = 20
    _PROF_MAX = 200

    def __init__(self, orientation: str = 'horizontal', parent=None):
        super().__init__(parent)
        self._orientation = orientation
        self._scale    = 1.0
        self._offset   = 0.0
        self._img_size = 1000
        self._profile: np.ndarray | None = None
        self._profile_start: int = 0   # 이미지 좌표에서 프로파일 시작 픽셀
        self._prof_size: int = 52   # 드래그로 조절 가능
        self._resizing = False

        self.setMouseTracking(True)
        self._update_fixed_size()
        self.setStyleSheet("background-color: #16213e;")

    def _update_fixed_size(self):
        total = self._prof_size + self._TICK
        if self._orientation == 'horizontal':
            self.setFixedHeight(total)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(total)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def _sep_pos(self) -> int:
        return self._prof_size

    def _on_sep(self, x: int, y: int) -> bool:
        sep = self._sep_pos()
        if self._orientation == 'horizontal':
            return abs(y - sep) <= self._SEP_HIT
        else:
            return abs(x - sep) <= self._SEP_HIT

    # ── 공개 API ────────────────────────────────────────────────────────

    def update_transform(self, scale: float, offset: float, img_size: int):
        self._scale    = scale
        self._offset   = offset
        self._img_size = img_size
        self.update()

    def set_profile(self, data: np.ndarray | None, data_start: int = 0):
        self._profile = data
        self._profile_start = data_start
        self.update()

    # ── 마우스 (구분선 드래그) ──────────────────────────────────────────

    def mousePressEvent(self, event):
        from PyQt6.QtCore import Qt
        if event.button() == Qt.MouseButton.LeftButton and self._on_sep(event.pos().x(), event.pos().y()):
            self._resizing = True
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        from PyQt6.QtCore import Qt
        x, y = event.pos().x(), event.pos().y()
        if self._resizing:
            if self._orientation == 'horizontal':
                new_sz = max(self._PROF_MIN, min(self._PROF_MAX, y))
            else:
                new_sz = max(self._PROF_MIN, min(self._PROF_MAX, x))
            self._prof_size = new_sz
            self._update_fixed_size()
            self.update()
            event.accept()
        else:
            if self._on_sep(x, y):
                cur = Qt.CursorShape.SizeVerCursor if self._orientation == 'horizontal' else Qt.CursorShape.SizeHorCursor
                self.setCursor(cur)
            else:
                self.unsetCursor()
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def leaveEvent(self, ev):
        if self._resizing:
            self._resizing = False
            self.unsetCursor()
        super().leaveEvent(ev)

    # ── 페인트 ──────────────────────────────────────────────────────────

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QPen, QFont
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        W, H = self.width(), self.height()
        PR, TK = self._prof_size, self._TICK

        c_bg_prof = QColor(0x0e, 0x16, 0x24)
        c_bg_tick = QColor(0x16, 0x21, 0x3e)
        c_tick    = QColor(0x50, 0x60, 0x80)
        c_text    = QColor(0xa0, 0xa0, 0xb0)
        c_sep     = QColor(0x1a, 0x30, 0x60)
        font      = QFont('Segoe UI', 8)
        p.setFont(font)

        # ── 눈금 간격 ────────────────────────────────────────────
        target_px = 60
        raw_step  = target_px / max(self._scale, 0.001)
        magnitude = 10 ** int(np.log10(max(raw_step, 1)))
        tick_step = magnitude * 10
        for s in [magnitude, magnitude * 2, magnitude * 5, magnitude * 10]:
            if s * self._scale >= target_px:
                tick_step = s
                break
        start_tick = (int(self._offset) // tick_step) * tick_step

        if self._orientation == 'horizontal':
            # 프로파일 영역 (위)
            p.fillRect(0, 0, W, PR, c_bg_prof)
            self._draw_h_profile(p, W, PR)
            p.setPen(QPen(c_sep, 1))
            p.drawLine(0, PR, W, PR)

            # 눈금 영역 (아래)
            p.fillRect(0, PR, W, TK, c_bg_tick)
            p.setPen(QPen(QColor(0x30, 0x40, 0x60), 1))
            p.drawLine(0, PR + TK - 1, W, PR + TK - 1)

            img_px = start_tick
            while True:
                sx = int((img_px - self._offset) * self._scale)
                if sx > W:
                    break
                if img_px >= 0:
                    p.setPen(QPen(c_tick, 1))
                    p.drawLine(sx, PR + TK - 8, sx, PR + TK - 1)
                    p.setPen(c_text)
                    p.drawText(sx + 2, PR + TK - 2, str(img_px))
                img_px += tick_step

        else:  # vertical
            # 프로파일 영역 (왼쪽)
            p.fillRect(0, 0, PR, H, c_bg_prof)
            self._draw_v_profile(p, H, PR)
            p.setPen(QPen(c_sep, 1))
            p.drawLine(PR, 0, PR, H)

            # 눈금 영역 (오른쪽)
            p.fillRect(PR, 0, TK, H, c_bg_tick)
            p.setPen(QPen(QColor(0x30, 0x40, 0x60), 1))
            p.drawLine(PR + TK - 1, 0, PR + TK - 1, H)

            img_px = start_tick
            while True:
                sy = int((img_px - self._offset) * self._scale)
                if sy > H:
                    break
                if img_px >= 0:
                    p.setPen(QPen(c_tick, 1))
                    p.drawLine(PR, sy, PR + 8, sy)
                    p.setPen(c_text)
                    p.save()
                    p.translate(PR + TK - 2, sy - 2)
                    p.rotate(-90)
                    p.drawText(0, 0, str(img_px))
                    p.restore()
                img_px += tick_step

        p.end()

    # ── 프로파일 그리기 ─────────────────────────────────────────────────

    def _draw_h_profile(self, p, W: int, area_h: int):
        """수평 룰러 프로파일 — 열(column) 강도, 높을수록 아래(이미지 방향)."""
        from PyQt6.QtGui import QPen, QColor, QPainterPath
        if self._profile is None or len(self._profile) == 0:
            return
        data = self._profile
        n    = len(data)
        dmin = float(data.min())
        dmax = float(data.max())
        if dmax <= dmin:
            return

        pad  = 3
        span = area_h - 2 * pad
        ps = self._profile_start   # 이미지 좌표 오프셋

        # 화면에 보이는 이미지 좌표 범위
        img_view_lo = self._offset
        img_view_hi = self._offset + W / max(self._scale, 1e-6)
        # 데이터 배열 범위로 변환 (배열 인덱스 = 이미지좌표 - ps)
        d0 = max(0, int(img_view_lo - ps) - 1)
        d1 = min(n - 1, int(img_view_hi - ps) + 1)
        if d0 > d1:
            return

        step = max(1, (d1 - d0) // max(W, 1))

        # 피크 탐색 (보이는 구간)
        visible = data[d0:d1 + 1]
        peak_arr_idx = int(d0 + np.argmax(visible))
        peak_val = float(data[peak_arr_idx])
        peak_img_idx = peak_arr_idx + ps   # 이미지 좌표

        path  = QPainterPath()
        first = True
        for i in range(d0, d1 + 1, step):
            img_idx = i + ps   # 이미지 좌표
            sx = (img_idx - self._offset) * self._scale
            norm = (data[i] - dmin) / (dmax - dmin)
            sy = pad + (1.0 - norm) * span   # 높을수록 위
            if first:
                path.moveTo(sx, sy)
                first = False
            else:
                path.lineTo(sx, sy)

        if not first:
            p.setRenderHint(p.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor('#d4691e'), 1))
            p.drawPath(path)

            # 피크(max) 마커 + 수치
            pk_sx = (peak_img_idx - self._offset) * self._scale
            pk_sy = pad + (1.0 - (peak_val - dmin) / (dmax - dmin)) * span
            p.setPen(QPen(QColor('#ffcc44'), 1))
            p.setBrush(QColor('#ffcc44'))
            p.drawEllipse(int(pk_sx) - 3, int(pk_sy) - 3, 6, 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor('#ffcc44'))
            p.drawText(int(pk_sx) + 5, max(12, int(pk_sy) - 2), f"{peak_val:.0f}")

            # min/max 범위 라벨 (우측 고정)
            fm = p.fontMetrics()
            max_lbl = f"{dmax:.0f}"
            min_lbl = f"{dmin:.0f}"
            max_lbl_w = fm.horizontalAdvance(max_lbl)
            min_lbl_w = fm.horizontalAdvance(min_lbl)
            lbl_x_max = W - max_lbl_w - 2
            lbl_x_min = W - min_lbl_w - 2
            p.setPen(QColor(0xa0, 0xb0, 0xc0))
            p.drawText(lbl_x_max, pad + 10, max_lbl)           # 위 = max (norm=1 → sy=pad)
            p.drawText(lbl_x_min, pad + span - 2, min_lbl)     # 아래 = min (norm=0 → sy=pad+span)

            p.setRenderHint(p.RenderHint.Antialiasing, False)

    def _draw_v_profile(self, p, H: int, area_w: int):
        """수직 룰러 프로파일 — 행(row) 강도, 높을수록 왼쪽(이미지 반대 방향)."""
        from PyQt6.QtGui import QPen, QColor, QPainterPath
        if self._profile is None or len(self._profile) == 0:
            return
        data = self._profile
        n    = len(data)
        dmin = float(data.min())
        dmax = float(data.max())
        if dmax <= dmin:
            return

        pad  = 3
        span = area_w - 2 * pad
        ps = self._profile_start

        img_view_lo = self._offset
        img_view_hi = self._offset + H / max(self._scale, 1e-6)
        d0 = max(0, int(img_view_lo - ps) - 1)
        d1 = min(n - 1, int(img_view_hi - ps) + 1)
        if d0 > d1:
            return

        step = max(1, (d1 - d0) // max(H, 1))

        # 피크 탐색
        visible = data[d0:d1 + 1]
        peak_arr_idx = int(d0 + np.argmax(visible))
        peak_val = float(data[peak_arr_idx])
        peak_img_idx = peak_arr_idx + ps

        path  = QPainterPath()
        first = True
        for i in range(d0, d1 + 1, step):
            img_idx = i + ps
            sy = (img_idx - self._offset) * self._scale
            norm = (data[i] - dmin) / (dmax - dmin)
            sx = pad + (1.0 - norm) * span   # 높을수록 왼쪽
            if first:
                path.moveTo(sx, sy)
                first = False
            else:
                path.lineTo(sx, sy)

        if not first:
            p.setRenderHint(p.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor('#d4691e'), 1))
            p.drawPath(path)

            # 피크(max) 마커 + 수치
            pk_sy = (peak_img_idx - self._offset) * self._scale
            pk_sx = pad + (1.0 - (peak_val - dmin) / (dmax - dmin)) * span
            p.setPen(QPen(QColor('#ffcc44'), 1))
            p.setBrush(QColor('#ffcc44'))
            p.drawEllipse(int(pk_sx) - 3, int(pk_sy) - 3, 6, 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor('#ffcc44'))
            p.drawText(max(2, int(pk_sx) - 30), int(pk_sy) - 4, f"{peak_val:.0f}")

            # min/max 범위 라벨 (상단/하단 고정)
            p.setPen(QColor(0xa0, 0xb0, 0xc0))
            fm = p.fontMetrics()
            max_lbl = f"{dmax:.0f}"
            min_lbl = f"{dmin:.0f}"
            # dmax → sx=pad (왼쪽 끝), dmin → sx=pad+span (오른쪽 끝)
            p.drawText(pad, 12, max_lbl)
            p.drawText(max(pad, area_w - fm.horizontalAdvance(min_lbl) - 2), 12, min_lbl)

            p.setRenderHint(p.RenderHint.Antialiasing, False)


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
            "color:#4a6a8a; font-family:'Courier New'; font-size:10px;"
            " font-weight:bold; letter-spacing:2px; background:transparent;"
        )
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setStyleSheet(
            "QPushButton{background:transparent;color:#506080;border:none;font-size:13px;}"
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
    range_changed        = pyqtSignal(float, float)
    colormap_changed     = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_image: np.ndarray | None = None
        self._current_cmap = 'off'
        self._crosshair_color = '#ff0000'
        self._last_click_info = None  # (ix, iy, val)
        self._sel_box_rect: tuple | None = None   # (x0,y0,x1,y1) 이미지 좌표
        self._roi_line_pts = None
        self._roi_box_pts  = None
        self._roi_hist_pts = None
        self._roi_mode = None
        # 패널에 연결된 ROI id 추적
        self._active_profile_roi_id: int | None = None
        self._active_hist_roi_id:    int | None = None
        self._last_profile_t: float = 0.0   # profile throttle용 타임스탬프
        self._colormap_worker = None
        self._pending_workers: set = set()   # GC 방지용 실행중 워커 참조
        self._display_vmin: float | None = None
        self._display_vmax: float | None = None
        self._external_render_control = False  # True면 외부(예: LiveTab) 렌더 파이프라인 사용
        self._rotation_k: int = 0              # np.rot90 k 값 (0/1/2/3 → 0°/90°/180°/270°)
        self._range_debounce = QTimer()
        self._range_debounce.setSingleShot(True)
        self._range_debounce.setInterval(50)
        self._range_debounce.timeout.connect(lambda: self._refresh_pixmap(fit=False))
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

        # Range 슬라이더 패널 토글
        self.btn_range = QToolButton()
        self.btn_range.setText("📊 Range")
        self.btn_range.setCheckable(True)
        self.btn_range.setToolTip("컬러맵 Min/Max 히스토그램 슬라이더 표시")
        self.btn_range.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:checked {
                background: #1a3a20; color: #4ecdc4; border-color: #4ecdc4;
            }
            QToolButton:hover { border-color: #4ecdc4; }
        """)
        self.btn_range.toggled.connect(self._on_range_panel_toggled)
        toolbar.addWidget(self.btn_range)

        # ROI 목록 토글
        self.btn_roi_list_toggle = QToolButton()
        self.btn_roi_list_toggle.setText("📋 ROI 목록")
        self.btn_roi_list_toggle.setCheckable(True)
        self.btn_roi_list_toggle.setToolTip("그려진 ROI 목록 표시 / 삭제")
        self.btn_roi_list_toggle.setStyleSheet("""
            QToolButton {
                background: transparent; color: #a0a0b0;
                border: 1px solid #0f3460; border-radius: 3px;
                padding: 2px 6px; font-size: 11px;
            }
            QToolButton:checked { background: #1a2840; color: #ffe66d; border-color: #ffe66d; }
            QToolButton:hover { border-color: #ffe66d; }
        """)
        self.btn_roi_list_toggle.toggled.connect(self._on_roi_list_toggled)
        toolbar.addWidget(self.btn_roi_list_toggle)

        toolbar.addStretch()

        # 회전
        rotate_label = QLabel("↻")
        rotate_label.setStyleSheet("color: #a0a0b0; font-size: 13px; margin-right:0px;")
        toolbar.addWidget(rotate_label)
        self.rotate_combo = QComboBox()
        self.rotate_combo.addItems(["0°", "90°", "180°", "270°"])
        self.rotate_combo.setFixedWidth(58)
        self.rotate_combo.setStyleSheet("""
            QComboBox {
                background:#080e1e; border:1px solid #1a3a60; color:#d0deff;
                border-radius:3px; font-size:11px; padding:1px 4px; min-height:20px;
            }
            QComboBox::drop-down { border:none; width:14px; }
            QComboBox QAbstractItemView {
                background:#0a1428; color:#d0deff;
                selection-background-color:#1a3a60;
            }
        """)
        self.rotate_combo.currentIndexChanged.connect(self._on_rotation_changed)
        toolbar.addWidget(self.rotate_combo)

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

        # ── ROI 목록 패널 (토글) ──
        self._roi_list_panel = QWidget()
        self._roi_list_panel.setVisible(False)
        self._roi_list_panel.setFixedHeight(90)
        self._roi_list_panel.setStyleSheet(
            "background:#0d1828; border-bottom:1px solid #0f3460;"
        )
        roi_panel_row = QHBoxLayout(self._roi_list_panel)
        roi_panel_row.setContentsMargins(6, 4, 6, 4)
        roi_panel_row.setSpacing(6)

        self._roi_list_widget = QListWidget()
        self._roi_list_widget.setStyleSheet("""
            QListWidget {
                background:#080e1e; color:#c0d0ff;
                border:1px solid #0f3460; font-family:'Courier New'; font-size:11px;
            }
            QListWidget::item { padding:2px 4px; }
            QListWidget::item:selected { background:#1a3a60; color:#ffe66d; }
        """)
        roi_panel_row.addWidget(self._roi_list_widget, 1)

        _roi_del_btn = (
            "QPushButton { background:#0d1a28; color:#e94560;"
            "border:1px solid #e94560; border-radius:3px;"
            "font-size:11px; padding:3px 8px; }"
            "QPushButton:hover { background:#2a1020; }"
        )
        roi_btn_col = QVBoxLayout()
        roi_btn_col.setSpacing(4)
        roi_btn_col.setContentsMargins(0, 0, 0, 0)
        self._btn_del_roi = QPushButton("선택 삭제")
        self._btn_del_all_roi = QPushButton("전체 삭제")
        self._btn_del_roi.setFixedWidth(80)
        self._btn_del_all_roi.setFixedWidth(80)
        self._btn_del_roi.setStyleSheet(_roi_del_btn)
        self._btn_del_all_roi.setStyleSheet(_roi_del_btn)
        self._btn_del_roi.clicked.connect(self._delete_selected_roi)
        self._btn_del_all_roi.clicked.connect(self._delete_all_rois_ui)
        roi_btn_col.addWidget(self._btn_del_roi)
        roi_btn_col.addWidget(self._btn_del_all_roi)
        roi_btn_col.addStretch()
        roi_panel_row.addLayout(roi_btn_col)
        layout.addWidget(self._roi_list_panel)

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
        self._view.sel_box_changed.connect(self._on_sel_box_changed)
        self._view.roi_drawn.connect(self._on_roi_drawn)
        self._view.scale_changed.connect(self._on_scale_changed)
        # ROI 선택/추가 콜백
        self._view.on_roi_selected = self._on_roi_selected
        self._view.on_roi_added    = self._on_roi_added
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

        # ── 히스토그램 Range 슬라이더 팝업 ──
        self._hist_range_widget = HistogramRangeWidget()
        self._hist_range_widget.range_changed.connect(self._on_range_changed)
        self._hist_range_widget.range_changed.connect(self.range_changed)
        self._range_popup = _RangePopup(self._hist_range_widget, parent=self)
        self._range_popup.closed.connect(lambda: self.btn_range.setChecked(False))

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        """프레임 전환 (뷰 유지)"""
        self._current_image = image
        self._refresh_pixmap(fit=False)
        self._recompute_profile()
        self._update_ruler_profiles()
        if self._hist_range_widget.isVisible():
            self._hist_range_widget.update_image(image, self._current_cmap)

    def set_image_first(self, image: np.ndarray):
        """첫 로드 (뷰 fit) — range 리셋"""
        self._current_image = image
        self._display_vmin = None
        self._display_vmax = None
        if self._hist_range_widget.isVisible():
            self._hist_range_widget.update_image(image, self._current_cmap,
                                                 reset_range=True)
        self._refresh_pixmap(fit=True)
        self._update_ruler_profiles()

    def set_live_frame(self, rgb: np.ndarray, fit: bool = False):
        """라이브 스트리밍 전용 — ColorMapWorker 우회, GUI 스레드에서 직접 변환.
        rgb: uint8 H×W×3 (또는 H×W grayscale)
        프로파일/히스토그램은 최대 10fps로 throttle — scipy/numpy 과부하 방지.
        """
        import time
        from PyQt6.QtGui import QImage, QPixmap
        # 라이브에서는 set_source_image(raw)가 먼저 들어오면 그 값을 유지한다.
        # (프로파일/룰러/픽셀값은 raw 기준)
        if self._current_image is None:
            self._current_image = rgb
        disp = np.rot90(rgb, k=self._rotation_k) if self._rotation_k else rgb
        h, w = disp.shape[:2]
        if disp.ndim == 3 and disp.shape[2] == 3:
            disp_c = np.ascontiguousarray(disp)
            qimg = QImage(disp_c.data, w, h, w * 3, QImage.Format.Format_RGB888)
        else:
            disp_c = np.ascontiguousarray(disp)
            qimg = QImage(disp_c.data, w, h, w, QImage.Format.Format_Grayscale8)
        self._view.set_pixmap(QPixmap.fromImage(qimg.copy()), w, h, fit=fit)
        # ROI 프로파일/히스토그램 + 룰러: 최대 10fps (0.1초 간격)
        now = time.monotonic()
        if now - self._last_profile_t >= 0.1:
            self._last_profile_t = now
            self._recompute_profile()
            self._update_ruler_profiles()

    def set_source_image(self, img: np.ndarray) -> None:
        """라이브 모드에서 colormap/export 기준이 될 원본 grayscale 이미지를 갱신.
        set_live_frame은 display용 RGB를 받으므로, 원본은 별도로 저장해야
        _refresh_pixmap/_export_image에서 이중 colormap 적용이 발생하지 않는다."""
        self._current_image = img

    def set_centroid_overlay(self, cx: float, cy: float) -> None:
        """센트로이드 위치에 십자 마커를 씬 오버레이로 표시 (픽셀에 굽지 않음)."""
        self.clear_centroid_overlay()
        from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsSimpleTextItem
        pen = QPen(QColor('#4ecdc4'), 2)
        pen.setCosmetic(True)
        arm = 20
        h = QGraphicsLineItem(cx - arm, cy, cx + arm, cy)
        v = QGraphicsLineItem(cx, cy - arm, cx, cy + arm)
        h.setPen(pen); h.setZValue(10)
        v.setPen(pen); v.setZValue(10)
        txt = QGraphicsSimpleTextItem(f"({cx:.1f}, {cy:.1f})")
        txt.setBrush(QBrush(QColor('#4ecdc4')))
        txt.setZValue(10)
        txt.setFlag(txt.GraphicsItemFlag.ItemIgnoresTransformations)
        txt.setPos(cx + 6, cy - 6)
        for item in (h, v, txt):
            self._view._scene.addItem(item)
        self._centroid_overlay_items = [h, v, txt]

    def clear_centroid_overlay(self) -> None:
        """센트로이드 오버레이 제거."""
        for item in getattr(self, '_centroid_overlay_items', []):
            if item.scene():
                self._view._scene.removeItem(item)
        self._centroid_overlay_items = []

    def set_external_render_control(self, enabled: bool) -> None:
        """외부 렌더 파이프라인 사용 여부를 설정한다.

        True  : range/cmap 변경 시 내부 _refresh_pixmap 호출을 생략.
        False : 기존 동작(내부 ColorMapWorker 렌더) 사용.
        """
        self._external_render_control = bool(enabled)

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
        # 이전 워커: 결과 시그널만 끊어 화면 업데이트 방지.
        # 스레드 자체는 계속 실행되며 finished → _pending_workers 제거 → deleteLater 순으로 자율 정리.
        if self._colormap_worker is not None:
            try:
                self._colormap_worker.colormap_applied.disconnect()
            except Exception:
                pass

        img = self._current_image
        # 회전 적용 (k=1 → 90°CCW, k=2 → 180°, k=3 → 270°CCW)
        if self._rotation_k:
            img = np.rot90(img, k=self._rotation_k)
        cmap = self._current_cmap

        from core.async_worker import ColorMapWorker
        worker = ColorMapWorker(img, cmap,
                                vmin=self._display_vmin, vmax=self._display_vmax)
        self._colormap_worker = worker
        self._pending_workers.add(worker)   # Python 레퍼런스 유지 → GC 방지

        def _cleanup():
            self._pending_workers.discard(worker)

        worker.finished.connect(_cleanup)
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
        img = self._current_image
        if self._rotation_k:
            img = np.rot90(img, k=self._rotation_k)
        h, w = img.shape[:2]
        x_img_offset = x_offset / max(scale, 0.001)
        y_img_offset = y_offset / max(scale, 0.001)
        self._ruler_x.update_transform(scale, x_img_offset, w)
        self._ruler_y.update_transform(scale, y_img_offset, h)

    def _update_ruler_profiles(self):
        """선택 박스 > 마지막 클릭 위치 > 전체 평균 순으로 룰러 프로파일 전달."""
        if self._current_image is None:
            self._ruler_x.set_profile(None)
            self._ruler_y.set_profile(None)
            return
        img = self._current_image
        H0, W0 = img.shape[:2]
        if self._rotation_k:
            img = np.rot90(img, k=self._rotation_k)
        rH, rW = img.shape[:2]

        # 1순위: 선택 박스
        if self._sel_box_rect is not None:
            if self._update_ruler_profiles_sel(img, rH, rW):
                return

        if self._last_click_info is not None:
            ix0, iy0, _ = self._last_click_info  # 원본 이미지 좌표
            k = self._rotation_k
            if k == 0:
                rix, riy = ix0, iy0
            elif k == 1:   # 90° CCW
                riy, rix = W0 - 1 - ix0, iy0
            elif k == 2:   # 180°
                riy, rix = H0 - 1 - iy0, W0 - 1 - ix0
            else:          # 270° CCW
                riy, rix = ix0, H0 - 1 - iy0
            rix = max(0, min(rW - 1, rix))
            riy = max(0, min(rH - 1, riy))
            if img.ndim == 2:
                x_prof = img[riy, :].astype(np.float32)
                y_prof = img[:, rix].astype(np.float32)
            else:
                x_prof = img[riy, :].mean(axis=-1).astype(np.float32)
                y_prof = img[:, rix].mean(axis=-1).astype(np.float32)
        else:
            if img.ndim == 2:
                x_prof = img.mean(axis=0).astype(np.float32)
                y_prof = img.mean(axis=1).astype(np.float32)
            elif img.ndim == 3:
                x_prof = img.mean(axis=(0, 2)).astype(np.float32)
                y_prof = img.mean(axis=(1, 2)).astype(np.float32)
            else:
                return
        self._ruler_x.set_profile(x_prof, 0)
        self._ruler_y.set_profile(y_prof, 0)

    def _update_ruler_profiles_sel(self, img: np.ndarray, rH: int, rW: int):
        """선택 박스 기준 룰러 프로파일 계산.

        X 룰러: 박스 Y범위(H)만 평균 → 전체 폭 프로파일
        Y 룰러: 박스 X범위(W)만 평균 → 전체 높이 프로파일
        """
        x0, y0, x1, y1 = self._sel_box_rect
        ix0 = max(0, min(int(x0), rW - 1))
        iy0 = max(0, min(int(y0), rH - 1))
        ix1 = max(ix0 + 1, min(int(x1), rW))
        iy1 = max(iy0 + 1, min(int(y1), rH))
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        if img.ndim == 2:
            x_prof = img[iy0:iy1, :].mean(axis=0).astype(np.float32)   # 전체 폭, 박스 H 평균
            y_prof = img[:, ix0:ix1].mean(axis=1).astype(np.float32)   # 전체 높이, 박스 W 평균
        else:
            x_prof = img[iy0:iy1, :].mean(axis=(0, 2)).astype(np.float32)
            y_prof = img[:, ix0:ix1].mean(axis=(1, 2)).astype(np.float32)
        self._ruler_x.set_profile(x_prof, 0)
        self._ruler_y.set_profile(y_prof, 0)
        return True

    # ─────────────────────────────────────────
    # ROI 선택 → 패널 갱신
    # ─────────────────────────────────────────

    def _on_roi_selected(self, roi_id):
        """클릭 또는 신규 드로우로 ROI가 선택됐을 때 해당 타입 패널을 갱신한다."""
        # 목록이 열려있으면 동기화
        if self.btn_roi_list_toggle.isChecked():
            self._refresh_roi_list()
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
    # ROI 목록 패널
    # ─────────────────────────────────────────

    def _on_roi_list_toggled(self, checked: bool):
        self._roi_list_panel.setVisible(checked)
        if checked:
            self._refresh_roi_list()

    def _on_roi_added(self, roi):
        if self.btn_roi_list_toggle.isChecked():
            self._refresh_roi_list()

    def _refresh_roi_list(self):
        self._roi_list_widget.clear()
        _icon = {'Line': '━', 'Box': '▭', 'Hist': '▒'}
        for roi_id, roi in self._view._rois.items():
            icon = _icon.get(roi.roi_type, '?')
            try:
                (x0, y0), (x1, y1) = roi.pts[0], roi.pts[1]
                coord = f"  ({int(x0)},{int(y0)})→({int(x1)},{int(y1)})"
            except Exception:
                coord = ""
            item = QListWidgetItem(f"{icon} {roi.roi_type} #{roi_id}{coord}")
            item.setData(Qt.ItemDataRole.UserRole, roi_id)
            self._roi_list_widget.addItem(item)
            if roi_id == self._view._selected_roi_id:
                self._roi_list_widget.setCurrentItem(item)

    def _delete_selected_roi(self):
        item = self._roi_list_widget.currentItem()
        if item is None:
            return
        roi_id = item.data(Qt.ItemDataRole.UserRole)
        if roi_id is None:
            return
        self._view.delete_roi(roi_id)
        if self._active_profile_roi_id == roi_id:
            self._active_profile_roi_id = None
            self._roi_line_pts = None
            self._roi_box_pts  = None
        if self._active_hist_roi_id == roi_id:
            self._active_hist_roi_id = None
            self._roi_hist_pts = None
        self._refresh_roi_list()

    def _delete_all_rois_ui(self):
        self._view.delete_all_rois()
        self._active_profile_roi_id = None
        self._active_hist_roi_id    = None
        self._roi_line_pts = None
        self._roi_box_pts  = None
        self._roi_hist_pts = None
        self._refresh_roi_list()

    # ─────────────────────────────────────────
    # 회전
    # ─────────────────────────────────────────

    def _on_rotation_changed(self, index: int):
        self._rotation_k = index   # 0/1/2/3 → 0°/90°/180°/270°
        if self._current_image is not None:
            self._refresh_pixmap(fit=False)
            self._update_ruler_profiles()

    def set_rotation(self, degrees: int):
        """외부에서 회전 각도를 설정 (0/90/180/270)."""
        k = (degrees // 90) % 4
        self._rotation_k = k
        self.rotate_combo.setCurrentIndex(k)

    # ─────────────────────────────────────────
    # 크로스헤어 / 컬러맵
    # ─────────────────────────────────────────

    def _on_crosshair_toggled(self, on: bool):
        self._view.set_crosshair(on)

    def _on_crosshair_color_changed(self, name: str):
        color = self._crosshair_colors.get(name, '#ff0000')
        self._crosshair_color = color
        self._view.set_crosshair_color(color)


    def _on_range_panel_toggled(self, checked: bool):
        if checked:
            btn_global = self.btn_range.mapToGlobal(
                self.btn_range.rect().bottomLeft()
            )
            self._range_popup.move(btn_global.x(), btn_global.y() + 4)
            if self._current_image is not None:
                self._hist_range_widget.update_image(
                    self._current_image, self._current_cmap, reset_range=True
                )
            self._range_popup.show()
            self._range_popup.raise_()
        else:
            self._range_popup.hide()

    def _on_range_changed(self, vmin: float, vmax: float):
        self._display_vmin = vmin
        self._display_vmax = vmax
        if not self._external_render_control:
            self._range_debounce.start()  # 50ms 후 렌더 — 빠른 드래그 시 중간 프레임 스킵

    def _on_cmap_changed(self, name: str):
        # 'Off'는 내부적으로 'off'로 처리
        self._current_cmap = name.lower() if name.lower() == 'off' else name
        is_off = (self._current_cmap == 'off')

        # Colormap Off에서는 Range UI를 자동으로 닫아 혼란을 줄인다.
        if is_off:
            if self.btn_range.isChecked():
                self.btn_range.setChecked(False)
            self.btn_range.setEnabled(False)
        else:
            self.btn_range.setEnabled(True)

        self.colormap_changed.emit(self._current_cmap)
        if self._hist_range_widget.isVisible() and self._current_image is not None:
            self._hist_range_widget.update_image(self._current_image, self._current_cmap)
        if not self._external_render_control:
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


    def _on_sel_box_changed(self, x0: float, y0: float, x1: float, y1: float):
        if x0 < 0:
            self._sel_box_rect = None
        else:
            self._sel_box_rect = (x0, y0, x1, y1)
        self._update_ruler_profiles()

    def _on_mouse_clicked(self, x: float, y: float):
        if x < 0 or y < 0:
            self._last_click_info = None
            self._update_ruler_profiles()   # 전체 평균으로 복귀
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
            self._update_ruler_profiles()

    # ─────────────────────────────────────────
    # 하위 호환 (main_window 에서 사용하는 속성들)
    # ─────────────────────────────────────────

    def autoRange(self):
        self._view.fit_to_view()

    @property
    def image_view(self):
        """하위 호환용 - main_window 에서 image_viewer.image_view.autoRange() 호출"""
        return self