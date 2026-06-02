from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFrame
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPixmap, QImage, QWheelEvent, QMouseEvent, QPainter
from ui.viewer_v2.viewer_state import ViewerState
from ui.viewer_v2.interaction_layer import InteractionLayer

class BaseView(QGraphicsView):
    """
    최하위 렌더링 계층. 
    이미지 표시, 줌, 팬 기능만 수행하며 ViewerState와 동기화됩니다.
    """
    mouse_moved = pyqtSignal(int, int, float)  # ix, iy, val

    def __init__(self, state: ViewerState, parent=None):
        super().__init__(parent)
        self._state = state
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)
        
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) # 좌상단 고정
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        
        # 상태 변경 감지
        self._state.view_transformed.connect(self._sync_to_state)
        
        # 인터랙션 레이어 추가
        self.interactions = InteractionLayer(self._state, self._scene, self)
        
        # 내부 상태
        self._img_data: np.ndarray | None = None
        self._current_qimg: QImage | None = None
        self._is_panning = False
        self._is_selecting = False
        self._is_syncing = False # 무한 루프 방지용 가드
        self._last_mouse_pos = QPointF()

    def set_image(self, pixmap: QPixmap, w: int, h: int, raw_data: np.ndarray | None = None):
        """새 이미지를 설정하고 씬 범위를 조절합니다."""
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(0, 0, w, h)
        self._img_data = raw_data
        self._state.img_width = w
        self._state.img_height = h
        
        # 현재 상태의 줌/팬 즉시 적용
        self._sync_to_state(self._state.scale, self._state.x_offset, self._state.y_offset)

    def _sync_to_state(self, scale: float, x_off: float, y_off: float):
        """외부(Hub)에서 상태가 변했을 때 내 화면을 맞춤."""
        if self._is_syncing: return
        self._is_syncing = True
        try:
            from PyQt6.QtGui import QTransform
            t = QTransform()
            t.scale(scale, scale)
            self.setTransform(t)
            self.horizontalScrollBar().setValue(int(x_off * scale))
            self.verticalScrollBar().setValue(int(y_off * scale))
        finally:
            self._is_syncing = False

    def _update_state(self):
        """내 조작으로 상태가 변했을 때 Hub에 알림."""
        if self._is_syncing: return
        self._is_syncing = True
        try:
            scale = self.transform().m11()
            # 스크롤바 값 = 현재 뷰포트에 보이는 이미지의 좌상단 픽셀 좌표
            x_off = self.horizontalScrollBar().value() / scale if scale > 0 else 0
            y_off = self.verticalScrollBar().value() / scale if scale > 0 else 0
            self._state.update_transform(scale, x_off, y_off)
        finally:
            self._is_syncing = False

    def wheelEvent(self, event: QWheelEvent):
        """마우스 커서 기준으로 확대/축소"""
        adj = 1.1 if event.angleDelta().y() > 0 else 0.9
        
        old_pos = self.mapToScene(event.position().toPoint())
        self.scale(adj, adj)
        new_pos = self.mapToScene(event.position().toPoint())
        
        delta = new_pos - old_pos
        self.translate(delta.x(), delta.y())
        
        self._update_state()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # 좌클릭: 포인트/ROI 시작
            self._is_selecting = True
            # [이벤트 소모 확인]
            # InteractionLayer가 이벤트를 처리했다면(예: 기본 ROI 이동), 
            # super().mousePressEvent를 호출하지 않아 뒤에 있는 아이템이 잡히는 것을 방지함.
            if self.interactions.start_action(self.mapToScene(event.pos())):
                return
        elif event.button() == Qt.MouseButton.RightButton:
            # 우클릭: 팬(Panning) 시작
            self._is_panning = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        scene_pos = self.mapToScene(event.pos())
        
        # 1. 정보바 업데이트 (좌표/밝기)
        w, h = self._state.img_width, self._state.img_height
        ix = max(0, min(w - 1, int(scene_pos.x())))
        iy = max(0, min(h - 1, int(scene_pos.y())))
        val = 0.0
        if self._img_data is not None:
            val = float(self._img_data[iy, ix])
        self.mouse_moved.emit(ix, iy, val)

        # 2. 인터랙션 처리
        if self._is_selecting:
            self.interactions.update_action(scene_pos, event.modifiers())
        elif self._is_panning:
            delta = event.position() - self._last_mouse_pos
            self._last_mouse_pos = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            self._update_state()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._is_selecting:
            self._is_selecting = False
            self.interactions.end_action(self.mapToScene(event.pos()), event.modifiers())
        
        if self._is_panning:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_selecting = False
            self.interactions.reset_action()
        super().mouseDoubleClickEvent(event)

    def fit_in_view(self):
        """이미지를 뷰포트 크기에 딱 맞춤 (비율 유지)"""
        if self._pixmap_item.pixmap().isNull(): return
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_state()

    def set_one_to_one(self):
        """1:1 배율로 설정"""
        self.resetTransform()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self._update_state()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.interactions.cancel_action()
        super().keyPressEvent(event)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._update_state()
