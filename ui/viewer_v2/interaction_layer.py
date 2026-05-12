from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsLineItem
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QObject
from PyQt6.QtGui import QPen, QColor, QBrush
from ui.viewer_v2.viewer_state import ViewerState
from ui.roi_items import LineROI, BoxROI, HistROI

class InteractionLayer(QObject):
    """
    고도화된 상호작용 계층. 
    영구적인 ROI(Line, Box, Hist) 생성 및 선택, 십자선 분석을 담당합니다.
    """
    roi_added = pyqtSignal(object)    # 신규 생성된 ROI 객체
    roi_selected = pyqtSignal(int)   # 선택된 ROI ID (None이면 해제)
    point_selected = pyqtSignal(QPointF)
    reset_requested = pyqtSignal()

    def __init__(self, state: ViewerState, scene, parent_view):
        super().__init__()
        self._state = state
        self._scene = scene
        self._view = parent_view
        
        self._rois: dict[int, object] = {}
        self._next_roi_id = 1
        self._selected_roi_id: int | None = None
        self._roi_mode: str | None = None  # 'line', 'box', 'histogram'
        
        # 드로잉 미리보기 아이템
        self._preview_item = None
        
        # 십자선 아이템 (레거시 스타일: 검정 외곽 + 흰 대시)
        self._cross_h = QGraphicsLineItem()
        self._cross_v = QGraphicsLineItem()
        self._cross_h.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.PenStyle.DashLine))
        self._cross_v.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.PenStyle.DashLine))
        self._cross_h.setZValue(100)
        self._cross_v.setZValue(100)
        self._cross_h.hide()
        self._cross_v.hide()
        self._scene.addItem(self._cross_h)
        self._scene.addItem(self._cross_v)
        
        # 기본 ROI (분석용 임시 사각형) - 전문 ROI보다 아래에 배치 (ZValue=10)
        self._basic_roi = QGraphicsRectItem()
        self._basic_roi.setPen(QPen(QColor("#4ecdc4"), 1, Qt.PenStyle.SolidLine))
        self._basic_roi.setBrush(QBrush(QColor(78, 205, 196, 40)))
        self._basic_roi.setZValue(10)
        self._basic_roi.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._basic_roi.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._basic_roi.hide()
        self._scene.addItem(self._basic_roi)
        
        # 상태 연결
        self._state.view_transformed.connect(self._on_view_transformed)
        self._state.crosshair_visible_changed.connect(self._sync_crosshair)
        self._state.roi_updated.connect(self._sync_basic_roi)

        self._is_drawing = False
        self._is_moving_basic = False
        self._start_pos = QPointF()
        self._start_basic_rect = QRectF()
        self._min_roi_size = 5

    def set_roi_mode(self, mode: str | None):
        """그리기 모드 설정"""
        self._roi_mode = mode
        if mode:
            self._view.setCursor(Qt.CursorShape.CrossCursor)
            self._deselect_all()
        else:
            self._view.setCursor(Qt.CursorShape.ArrowCursor)

    def _deselect_all(self):
        if self._selected_roi_id is not None:
            roi = self._rois.get(self._selected_roi_id)
            if roi: roi.select(False)
            self._selected_roi_id = None
            self.roi_selected.emit(None)

    def _on_view_transformed(self, scale, *args):
        """줌 변화 시 ROI 뱃지 및 핸들 크기 동기화"""
        for roi in self._rois.values():
            if hasattr(roi, 'update_handle_sizes'):
                roi.update_handle_sizes()
            if hasattr(roi, '_update_handles'):
                roi._update_handles()
        self._sync_crosshair()

    def _sync_crosshair(self):
        if not self._state.crosshair_visible:
            self._cross_h.hide()
            self._cross_v.hide()
            return
            
        x, y = self._state.crosshair_x, self._state.crosshair_y
        w, h = self._state.img_width, self._state.img_height
        self._cross_h.setLine(0, y, w, y)
        self._cross_v.setLine(x, 0, x, h)
        self._cross_h.show()
        self._cross_v.show()

    def _sync_basic_roi(self, rect: QRectF):
        """State의 기본 ROI와 비주얼 동기화"""
        if rect.isNull() or rect.width() < self._min_roi_size:
            self._basic_roi.hide()
        else:
            self._basic_roi.setRect(rect)
            self._basic_roi.show()

    def start_action(self, pos: QPointF) -> bool:
        """이벤트를 소모했다면 True 반환"""
        self._start_pos = pos
        from PyQt6.QtWidgets import QGraphicsPixmapItem
        from ui.roi_items import LineROI, BoxROI, HistROI, HandleItem
        
        # 1. 클릭 위치의 아이템 확인
        item = self._scene.itemAt(pos, self._view.transform())
        is_on_handle = isinstance(item, HandleItem)
        
        # [모드 1] 그리기 도구가 활성화된 상태 (Draw Mode)
        if self._roi_mode:
            # 핸들을 정확히 클릭했다면 -> 리사이즈 (드로잉 차단)
            if is_on_handle:
                self._is_drawing = False
                return False # Qt 기본 핸들 조작에 맡김
            
            # 그 외 모든 지역은 묻지도 따지지도 않고 신규 드로잉 시작 (박스 안의 박스 가능)
            self._is_drawing = True
            self._create_preview(pos)
            return True

        # [모드 2] 도구가 없는 상태 (Selection/Edit Mode)
        # 전문 ROI 또는 핸들 클릭 시 -> 이동/리사이즈 (드로잉 차단)
        is_on_pro = isinstance(item, (LineROI, BoxROI, HistROI, HandleItem)) or \
                    (item and item.parentItem() and isinstance(item.parentItem(), (LineROI, BoxROI, HistROI)))
        
        if is_on_pro:
            self._scene.clearSelection()
            if hasattr(item, 'setSelected'):
                item.setSelected(True)
            self._is_drawing = False
            self._is_moving_basic = False
            return False

        # 기본 ROI 클릭 시 -> 이동
        if item == self._basic_roi:
            self._is_drawing = False
            self._is_moving_basic = True
            self._start_basic_rect = self._state.selected_roi
            return True

        # 바닥 클릭 시 -> 신규 기본 ROI 드로잉
        self._is_moving_basic = False
        self._is_drawing = True
        self._state.update_roi(QRectF())
        self._create_preview(pos)
        return True

    def update_action(self, pos: QPointF):
        if self._is_drawing:
            self._update_preview(self._start_pos, pos)
            # 드래그 중에 실시간으로 State를 업데이트하면 렉이 발생하므로 비주얼만 업데이트함
        elif self._is_moving_basic:
            delta = pos - self._start_pos
            new_rect = self._start_basic_rect.translated(delta)
            # 드래그 중에는 비주얼만 업데이트 (렉 방지)
            self._basic_roi.setRect(new_rect)
            self._basic_roi.show()

    def end_action(self, pos: QPointF):
        if self._is_drawing:
            self._is_drawing = False
            
            # 드로잉이 끝난 시점에 최종 좌표를 State에 한 번만 반영 (연산 렉 방지)
            if not self._roi_mode:
                rect = QRectF(self._start_pos, pos).normalized()
                if rect.width() >= self._min_roi_size and rect.height() >= self._min_roi_size:
                    self._state.update_roi(rect)
                else:
                    self._state.update_roi(QRectF())
            
            if self._roi_mode:
                self._finalize_roi(self._start_pos, pos)
            self._clear_preview()
            
            dist = np.hypot(pos.x()-self._start_pos.x(), pos.y()-self._start_pos.y())
            if dist < self._min_roi_size and not self._roi_mode:
                self._state.update_roi(QRectF())
                self._state.update_crosshair(pos.x(), pos.y())
                self.point_selected.emit(pos)
        elif self._is_moving_basic:
            self._is_moving_basic = False
            # 이동이 끝난 시점에 최종 위치를 State에 한 번만 반영
            delta = pos - self._start_pos
            new_rect = self._start_basic_rect.normalized().translated(delta)
            self._state.update_roi(new_rect)
            self.roi_selected.emit(-1)

    def _create_preview(self, pos: QPointF):
        self._clear_preview()
        pen = QPen(QColor("#e94560"), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        if self._roi_mode == 'line':
            self._preview_item = QGraphicsLineItem(pos.x(), pos.y(), pos.x(), pos.y())
        else:
            self._preview_item = QGraphicsRectItem(QRectF(pos, pos))
            if self._roi_mode == 'histogram':
                pen.setColor(QColor("#4ecdc4"))
        
        self._preview_item.setPen(pen)
        self._scene.addItem(self._preview_item)

    def _update_preview(self, p0: QPointF, p1: QPointF):
        if not self._preview_item: return
        if self._roi_mode == 'line':
            self._preview_item.setLine(p0.x(), p0.y(), p1.x(), p1.y())
        else:
            self._preview_item.setRect(QRectF(p0, p1).normalized())

    def _clear_preview(self):
        if self._preview_item:
            self._scene.removeItem(self._preview_item)
            self._preview_item = None

    def _finalize_roi(self, p0: QPointF, p1: QPointF):
        dist = np.hypot(p1.x()-p0.x(), p1.y()-p0.y())
        if dist < self._min_roi_size: return

        roi_id = self._next_roi_id
        self._next_roi_id += 1
        
        scale_fn = lambda: self._state.scale
        if self._roi_mode == 'line':
            roi = LineROI(self._scene, scale_fn, roi_id, p0.x(), p0.y(), p1.x(), p1.y())
        elif self._roi_mode == 'box':
            roi = BoxROI(self._scene, scale_fn, roi_id, p0.x(), p0.y(), p1.x(), p1.y())
        elif self._roi_mode == 'histogram':
            roi = HistROI(self._scene, scale_fn, roi_id, p0.x(), p0.y(), p1.x(), p1.y())
        else:
            return

        roi.modified.connect(lambda: self._on_roi_modified(roi_id))
        
        # 전문 ROI는 항상 기본 ROI보다 위에 표시 (ZValue=100)
        if hasattr(roi, 'setZValue'):
            roi.setZValue(100)
        elif hasattr(roi, '_items'):
            for item in roi._items:
                item.setZValue(100)
                
        self._rois[roi_id] = roi
        self.roi_added.emit(roi)
        self._select_roi(roi_id)

    def _on_roi_modified(self, roi_id: int):
        # ROI 수정 시 필요한 처리 (데이터 재계산 등)
        pass

    def _try_select_roi(self, pos: QPointF):
        best_id = None
        best_dist = 10.0 / self._state.scale
        
        for rid, roi in self._rois.items():
            pts = roi.get_points() # x0, y0, x1, y1
            if roi.roi_type == 'Line':
                d = self._dist_point_to_line(pos, QPointF(pts[0], pts[1]), QPointF(pts[2], pts[3]))
            else:
                rect = QRectF(pts[0], pts[1], pts[2]-pts[0], pts[3]-pts[1]).normalized()
                d = 0 if rect.contains(pos) else 1000
                
            if d < best_dist:
                best_dist = d
                best_id = rid
        
        self._select_roi(best_id)

    def _select_roi(self, roi_id: int | None):
        if self._selected_roi_id == roi_id: return
        
        if self._selected_roi_id is not None:
            old = self._rois.get(self._selected_roi_id)
            if old: old.select(False)
            
        self._selected_roi_id = roi_id
        if roi_id is not None:
            new = self._rois.get(roi_id)
            if new: new.select(True)
            
        self.roi_selected.emit(roi_id)

    def _dist_point_to_line(self, p, a, b):
        pa = p - a
        ba = b - a
        t = np.clip(np.dot([pa.x(), pa.y()], [ba.x(), ba.y()]) / np.dot([ba.x(), ba.y()], [ba.x(), ba.y()]), 0, 1)
        dist_vec = pa - ba * t
        return np.hypot(dist_vec.x(), dist_vec.y())

    def delete_roi(self, roi_id: int):
        roi = self._rois.pop(roi_id, None)
        if roi:
            roi.remove()
        if self._selected_roi_id == roi_id:
            self._selected_roi_id = None
            self.roi_selected.emit(None)

    def reset_action(self):
        """더블 클릭 시 호출: 임시 분석 도구만 해제 (전문 ROI는 보존)"""
        self._deselect_all()
        self._state.update_roi(QRectF()) # 기본 ROI 해제
        self._state.toggle_crosshair(False)
        self.reset_requested.emit()

    def cancel_action(self):
        """그리기 도중 ESC 등을 눌러 취소할 때 호출"""
        if self._is_drawing:
            self._is_drawing = False
            self._clear_preview()
            # 기본 ROI 그리기 중이었다면 초기화
            if not self._roi_mode:
                self._state.update_roi(QRectF())
