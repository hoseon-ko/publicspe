"""
roi_items.py
QGraphicsItem 기반 ROI 클래스
- LineROI: 양 끝점 핸들로 이동/리사이즈
- BoxROI:  4모서리+4중간 핸들로 이동/리사이즈
- HistROI: BoxROI 동일 (청록색)

Active state (v2):
  set_active_profile(True/False) — 이 ROI가 Profile 패널에 피딩 중임을 강조
  set_active_hist(True/False)    — 이 ROI가 Histogram 패널에 피딩 중임을 강조
  deactivate()                   — 모든 강조 해제

시각:
  Line profile active  → 두꺼운 밝은 빨강 + 뒤에 글로우 라인 + ◆ PROFILE 배지
  Box  profile active  → 두꺼운 밝은 빨강 + 반투명 빨강 채우기 + ◆ PROFILE 배지
  Hist active          → 두꺼운 밝은 청록 + 반투명 청록 채우기 + ◆ HIST 배지
"""

from PyQt6.QtWidgets import (
    QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsSimpleTextItem,
)
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QObject
from PyQt6.QtGui import QPen, QColor, QBrush, QFont

HANDLE_SIZE = 8   # 핸들 크기 (화면 픽셀)

# ── 색상 팔레트 ───────────────────────────────────────────────────────────────

COLOR_LINE           = '#e94560'   # Line/Box ROI 기본 색
COLOR_BOX            = '#e94560'
COLOR_HIST           = '#4ecdc4'   # Hist ROI 기본 색
COLOR_SEL            = '#ffe66d'   # 선택 시 핸들 색 (노란색)

# Active 강조 색 (기본색보다 밝고 채도 높음)
COLOR_PROFILE_ACTIVE = '#ff2a4a'   # 밝은 빨강
COLOR_HIST_ACTIVE    = '#00f5e4'   # 밝은 청록

# Active 시 fill 색 (알파 포함)
_ALPHA_FILL  = 35   # 0-255, fill 투명도
_ALPHA_GLOW  = 55   # 글로우 투명도

BADGE_FONT = QFont('Segoe UI', 8, QFont.Weight.Bold)


def _glow_color(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# 핸들 아이템
# ─────────────────────────────────────────────────────────────────────────────

class HandleItem(QGraphicsEllipseItem):
    """드래그 가능한 핸들"""

    def __init__(self, role: str, parent_roi, scene_scale_fn, parent=None):
        super().__init__(parent)
        self.role = role
        self.parent_roi = parent_roi
        self._scene_scale_fn = scene_scale_fn
        self._dragging = False
        self._drag_start = None
        self._roi_start = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, False)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        pen = QPen(QColor(COLOR_SEL), 1)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(COLOR_SEL)))
        self._update_size()

    def _update_size(self):
        scale = self._scene_scale_fn()
        s = HANDLE_SIZE / max(scale, 0.1)
        self.setRect(-s/2, -s/2, s, s)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = ev.scenePos()
            self._roi_start = self.parent_roi.get_points()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._dragging:
            delta = ev.scenePos() - self._drag_start
            self.parent_roi.handle_drag(self.role, self._roi_start, delta)
            ev.accept()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.parent_roi.on_modified()
            ev.accept()


# ─────────────────────────────────────────────────────────────────────────────
# 베이스 ROI
# ─────────────────────────────────────────────────────────────────────────────

class BaseROI(QObject):
    modified = pyqtSignal()

    def __init__(self, scene, scale_fn, roi_id: int, color: str):
        super().__init__()
        self._scene   = scene
        self._scale_fn = scale_fn
        self.roi_id   = roi_id
        self.color    = color
        self._selected       = False
        self._profile_active = False
        self._hist_active    = False
        self._handles: list[HandleItem] = []
        self._items:   list = []
        self._badge_item: QGraphicsSimpleTextItem | None = None

    # ── active 공개 API ───────────────────────────────────────────────

    def set_active_profile(self, on: bool):
        """이 ROI가 Profile 패널에 연결됨을 강조."""
        self._profile_active = on
        if on:
            self._hist_active = False
        self._update_pen()
        self._update_badge()

    def set_active_hist(self, on: bool):
        """이 ROI가 Histogram 패널에 연결됨을 강조."""
        self._hist_active = on
        if on:
            self._profile_active = False
        self._update_pen()
        self._update_badge()

    def deactivate(self):
        """모든 active 강조 해제."""
        self._profile_active = False
        self._hist_active    = False
        self._update_pen()
        self._update_badge()

    @property
    def is_active(self) -> bool:
        return self._profile_active or self._hist_active

    # ── 선택 상태 ──────────────────────────────────────────────────────

    def select(self, on: bool):
        self._selected = on
        for h in self._handles:
            h.setVisible(on)
        self._update_pen()

    # ── 펜 생성 ────────────────────────────────────────────────────────

    def _make_pen(self, selected: bool = False) -> QPen:
        """
        active 상태 우선, 그 다음 selected, 마지막 normal.
        active 상태에서도 핸들 색(selected)은 HandleItem이 직접 처리하므로
        여기서는 ROI 본체 색만 결정.
        """
        if self._profile_active:
            color, width = COLOR_PROFILE_ACTIVE, 3
        elif self._hist_active:
            color, width = COLOR_HIST_ACTIVE, 3
        elif selected:
            color, width = COLOR_SEL, 2
        else:
            color, width = self.color, 2
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        return pen

    # ── 배지 텍스트 ────────────────────────────────────────────────────

    def _update_badge(self):
        """서브클래스가 오버라이드: 배지 위치/내용/가시성 갱신."""
        pass

    def _ensure_badge(self) -> QGraphicsSimpleTextItem:
        """배지 아이템을 필요 시 생성하여 반환."""
        if self._badge_item is None:
            self._badge_item = QGraphicsSimpleTextItem()
            self._badge_item.setFont(BADGE_FONT)
            # 줌에 무관하게 화면 픽셀 고정
            self._badge_item.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self._badge_item.setZValue(20)
            self._scene.addItem(self._badge_item)
        return self._badge_item

    def _hide_badge(self):
        if self._badge_item is not None:
            self._badge_item.setVisible(False)

    # ── 서브클래스 인터페이스 ────────────────────────────────────────────

    def _update_pen(self):
        pass

    def get_points(self):
        raise NotImplementedError

    def handle_drag(self, role, start_pts, delta):
        raise NotImplementedError

    def on_modified(self):
        self._update_handles()
        self.modified.emit()

    def _update_handles(self):
        pass

    # ── 삭제 ───────────────────────────────────────────────────────────

    def remove(self):
        for item in self._items:
            if item.scene():
                self._scene.removeItem(item)
        for h in self._handles:
            if h.scene():
                self._scene.removeItem(h)
        if self._badge_item is not None and self._badge_item.scene():
            self._scene.removeItem(self._badge_item)
        self._items.clear()
        self._handles.clear()
        self._badge_item = None

    def update_handle_sizes(self):
        for h in self._handles:
            h._update_size()


# ─────────────────────────────────────────────────────────────────────────────
# Line ROI
# ─────────────────────────────────────────────────────────────────────────────

class LineROI(BaseROI):
    roi_type = 'Line'

    def __init__(self, scene, scale_fn, roi_id: int, x0, y0, x1, y1):
        super().__init__(scene, scale_fn, roi_id, COLOR_LINE)
        self._x0, self._y0 = x0, y0
        self._x1, self._y1 = x1, y1
        self._glow_item: QGraphicsLineItem | None = None
        self._build()

    def _build(self):
        # ❶ 글로우 라인 (뒤에 배치, 기본 숨김)
        self._glow_item = QGraphicsLineItem(
            self._x0, self._y0, self._x1, self._y1)
        glow_pen = QPen(_glow_color(COLOR_PROFILE_ACTIVE, _ALPHA_GLOW), 8)
        glow_pen.setCosmetic(True)
        self._glow_item.setPen(glow_pen)
        self._glow_item.setZValue(-1)
        self._glow_item.setVisible(False)
        self._scene.addItem(self._glow_item)

        # ❷ 메인 라인
        self._line_item = QGraphicsLineItem(
            self._x0, self._y0, self._x1, self._y1)
        self._line_item.setPen(self._make_pen())
        self._line_item.setZValue(1)
        self._scene.addItem(self._line_item)
        self._items = [self._glow_item, self._line_item]

        # ❸ 핸들
        for role in ('p0', 'p1', 'move'):
            h = HandleItem(role, self, self._scale_fn)
            self._scene.addItem(h)
            h.setVisible(False)
            self._handles.append(h)

        self._update_handles()

    def _update_pen(self):
        self._line_item.setPen(self._make_pen(self._selected))
        self._update_glow()

    def _update_glow(self):
        """글로우 라인 업데이트 (active 시만 표시)."""
        if self._glow_item is None:
            return
        if self._profile_active:
            c = _glow_color(COLOR_PROFILE_ACTIVE, _ALPHA_GLOW)
        elif self._hist_active:
            c = _glow_color(COLOR_HIST_ACTIVE, _ALPHA_GLOW)
        else:
            self._glow_item.setVisible(False)
            return
        gpen = QPen(c, 8)
        gpen.setCosmetic(True)
        self._glow_item.setPen(gpen)
        self._glow_item.setVisible(True)

    def _update_badge(self):
        if self._profile_active:
            text  = '◆ PROFILE'
            color = COLOR_PROFILE_ACTIVE
        elif self._hist_active:
            text  = '◆ HIST'
            color = COLOR_HIST_ACTIVE
        else:
            self._hide_badge()
            return
        badge = self._ensure_badge()
        badge.setText(text)
        badge.setBrush(QBrush(QColor(color)))
        # 라인 끝점(p1) 근처에 배치 (화면 픽셀 기준 약간 오프셋)
        badge.setPos(self._x1, self._y1)
        badge.setVisible(True)

    def get_points(self):
        return (self._x0, self._y0, self._x1, self._y1)

    def handle_drag(self, role, start_pts, delta):
        x0, y0, x1, y1 = start_pts
        dx, dy = delta.x(), delta.y()
        if role == 'p0':
            self._x0, self._y0 = x0 + dx, y0 + dy
        elif role == 'p1':
            self._x1, self._y1 = x1 + dx, y1 + dy
        elif role == 'move':
            self._x0, self._y0 = x0 + dx, y0 + dy
            self._x1, self._y1 = x1 + dx, y1 + dy
        self._line_item.setLine(self._x0, self._y0, self._x1, self._y1)
        if self._glow_item:
            self._glow_item.setLine(self._x0, self._y0, self._x1, self._y1)
        self._update_handles()

    def _update_handles(self):
        pts = {
            'p0':   QPointF(self._x0, self._y0),
            'p1':   QPointF(self._x1, self._y1),
            'move': QPointF((self._x0+self._x1)/2, (self._y0+self._y1)/2),
        }
        for h in self._handles:
            h.setPos(pts[h.role])
            h._update_size()
        # 배지 위치도 갱신
        if self._badge_item is not None and self._badge_item.isVisible():
            self._badge_item.setPos(self._x1, self._y1)

    def remove(self):
        if self._glow_item is not None and self._glow_item.scene():
            self._scene.removeItem(self._glow_item)
            self._glow_item = None
        super().remove()

    @property
    def pts(self):
        return [(self._x0, self._y0), (self._x1, self._y1)]

    def label(self):
        return f"Line #{self.roi_id}"


# ─────────────────────────────────────────────────────────────────────────────
# Box ROI (Box / Hist 공통)
# ─────────────────────────────────────────────────────────────────────────────

class BoxROI(BaseROI):
    roi_type = 'Box'

    def __init__(self, scene, scale_fn, roi_id: int, x0, y0, x1, y1, color=None):
        super().__init__(scene, scale_fn, roi_id, color or COLOR_BOX)
        self._x0 = min(x0, x1)
        self._y0 = min(y0, y1)
        self._x1 = max(x0, x1)
        self._y1 = max(y0, y1)
        self._build()

    def _build(self):
        rect = QRectF(self._x0, self._y0,
                      self._x1 - self._x0, self._y1 - self._y0)
        self._rect_item = QGraphicsRectItem(rect)
        self._rect_item.setPen(self._make_pen())
        self._rect_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._rect_item.setZValue(1)
        self._scene.addItem(self._rect_item)
        self._items = [self._rect_item]

        for role in ('tl','tr','bl','br','tm','bm','lm','rm','move'):
            h = HandleItem(role, self, self._scale_fn)
            self._scene.addItem(h)
            h.setVisible(False)
            self._handles.append(h)

        self._update_handles()

    def _active_fill_brush(self) -> QBrush:
        """active 상태 시 반투명 채우기 브러시 반환."""
        if self._profile_active:
            c = _glow_color(COLOR_PROFILE_ACTIVE, _ALPHA_FILL)
        elif self._hist_active:
            c = _glow_color(COLOR_HIST_ACTIVE, _ALPHA_FILL)
        else:
            return QBrush(Qt.BrushStyle.NoBrush)
        return QBrush(c)

    def _update_pen(self):
        self._rect_item.setPen(self._make_pen(self._selected))
        self._rect_item.setBrush(self._active_fill_brush())

    def _update_badge(self):
        if self._profile_active:
            text  = '◆ PROFILE'
            color = COLOR_PROFILE_ACTIVE
        elif self._hist_active:
            text  = '◆ HIST'
            color = COLOR_HIST_ACTIVE
        else:
            self._hide_badge()
            return
        badge = self._ensure_badge()
        badge.setText(text)
        badge.setBrush(QBrush(QColor(color)))
        # 좌상단 모서리 위쪽에 배치
        badge.setPos(self._x0, self._y0)
        badge.setVisible(True)

    def get_points(self):
        return (self._x0, self._y0, self._x1, self._y1)

    def handle_drag(self, role, start_pts, delta):
        x0, y0, x1, y1 = start_pts
        dx, dy = delta.x(), delta.y()

        if role == 'tl':
            self._x0, self._y0 = x0+dx, y0+dy
        elif role == 'tr':
            self._x1, self._y0 = x1+dx, y0+dy
        elif role == 'bl':
            self._x0, self._y1 = x0+dx, y1+dy
        elif role == 'br':
            self._x1, self._y1 = x1+dx, y1+dy
        elif role == 'tm':
            self._y0 = y0+dy
        elif role == 'bm':
            self._y1 = y1+dy
        elif role == 'lm':
            self._x0 = x0+dx
        elif role == 'rm':
            self._x1 = x1+dx
        elif role == 'move':
            self._x0, self._y0 = x0+dx, y0+dy
            self._x1, self._y1 = x1+dx, y1+dy

        if self._x0 > self._x1:
            self._x0, self._x1 = self._x1, self._x0
        if self._y0 > self._y1:
            self._y0, self._y1 = self._y1, self._y0

        rect = QRectF(self._x0, self._y0,
                      self._x1 - self._x0, self._y1 - self._y0)
        self._rect_item.setRect(rect)
        self._update_handles()

    def _update_handles(self):
        mx = (self._x0 + self._x1) / 2
        my = (self._y0 + self._y1) / 2
        pts = {
            'tl': QPointF(self._x0, self._y0),
            'tr': QPointF(self._x1, self._y0),
            'bl': QPointF(self._x0, self._y1),
            'br': QPointF(self._x1, self._y1),
            'tm': QPointF(mx, self._y0),
            'bm': QPointF(mx, self._y1),
            'lm': QPointF(self._x0, my),
            'rm': QPointF(self._x1, my),
            'move': QPointF(mx, my),
        }
        cursors = {
            'tl': Qt.CursorShape.SizeFDiagCursor,
            'br': Qt.CursorShape.SizeFDiagCursor,
            'tr': Qt.CursorShape.SizeBDiagCursor,
            'bl': Qt.CursorShape.SizeBDiagCursor,
            'tm': Qt.CursorShape.SizeVerCursor,
            'bm': Qt.CursorShape.SizeVerCursor,
            'lm': Qt.CursorShape.SizeHorCursor,
            'rm': Qt.CursorShape.SizeHorCursor,
            'move': Qt.CursorShape.SizeAllCursor,
        }
        for h in self._handles:
            h.setPos(pts[h.role])
            h.setCursor(cursors[h.role])
            h._update_size()
        # 배지 위치 갱신
        if self._badge_item is not None and self._badge_item.isVisible():
            self._badge_item.setPos(self._x0, self._y0)

    @property
    def pts(self):
        return [(self._x0, self._y0), (self._x1, self._y1)]

    def label(self):
        return f"{self.roi_type} #{self.roi_id}"


class HistROI(BoxROI):
    roi_type = 'Hist'

    def __init__(self, scene, scale_fn, roi_id, x0, y0, x1, y1):
        super().__init__(scene, scale_fn, roi_id, x0, y0, x1, y1,
                         color=COLOR_HIST)

    def label(self):
        return f"Hist #{self.roi_id}"
