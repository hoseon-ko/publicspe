"""
image_viewer.py
pyqtgraph ImageView 기반 이미지 뷰어
ROI를 마우스 드래그로 직접 그리기
"""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox
from PyQt6.QtCore import pyqtSignal, Qt, QPointF, QTimer


class ImageViewer(QWidget):
    # 시그널
    line_profile_updated = pyqtSignal(object, str)        # (np.ndarray, label)
    box_profile_updated = pyqtSignal(object, object, str) # (x_mean, y_mean, label)
    pixel_info_updated = pyqtSignal(int, int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_image = None
        self._roi_mode = None
        self._draw_start = None

        self._line_item = None
        self._box_item = None
        self._roi_line_pts = None
        self._roi_box_pts = None

        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._on_resize_timeout)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 상단 툴바
        toolbar = QHBoxLayout()

        roi_label = QLabel("ROI:")
        roi_label.setStyleSheet("color: #a0a0b0; font-size: 11px;")
        toolbar.addWidget(roi_label)

        self.roi_combo = QComboBox()
        self.roi_combo.addItems(["None", "Line Profile", "Box Profile", "X Profile", "Y Profile"])
        self.roi_combo.setFixedWidth(130)
        self.roi_combo.currentTextChanged.connect(self._on_roi_mode_changed)
        toolbar.addWidget(self.roi_combo)

        cmap_label = QLabel("  Colormap:")
        cmap_label.setStyleSheet("color: #a0a0b0; font-size: 11px;")
        toolbar.addWidget(cmap_label)

        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(["jet", "viridis", "plasma", "inferno", "magma", "grey", "hot"])
        self.cmap_combo.setFixedWidth(100)
        self.cmap_combo.currentTextChanged.connect(self._on_cmap_changed)
        toolbar.addWidget(self.cmap_combo)

        toolbar.addStretch()

        self.pixel_label = QLabel("X: -  Y: -  Val: -")
        self.pixel_label.setStyleSheet("color: #e94560; font-size: 11px; font-weight: bold;")
        toolbar.addWidget(self.pixel_label)

        layout.addLayout(toolbar)

        # pyqtgraph ImageView
        self.image_view = pg.ImageView()
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        self.image_view.getHistogramWidget().setBackground('#16213e')

        view = self.image_view.getView()
        view.setMenuEnabled(False)
        view.setAspectLocked(True)

        layout.addWidget(self.image_view)

        # 마우스 이벤트 연결
        self.image_view.scene.sigMouseMoved.connect(self._on_mouse_moved)

        # mouseDragEvent 오버라이드
        vb = self.image_view.getView()
        self._orig_mouse_drag = vb.mouseDragEvent
        vb.mouseDragEvent = self._on_mouse_drag

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        self._current_image = image
        self.image_view.setImage(
            image.T,
            autoRange=False,
            autoLevels=True,
            autoHistogramRange=True
        )
        self._recompute_profile()

    def set_image_first(self, image: np.ndarray):
        self._current_image = image
        self.image_view.setImage(
            image.T,
            autoRange=True,
            autoLevels=True,
            autoHistogramRange=True
        )
        self._set_colormap(self.cmap_combo.currentText())
        self._clear_roi_items()
        self._fit_to_view(image)

    def _fit_to_view(self, image: np.ndarray):
        """이미지를 뷰어 위젯 크기에 비율 맞게 fit"""
        img_h, img_w = image.shape[:2]
        img_ratio = img_w / img_h  # 이미지 가로세로 비율

        # 실제 뷰어 영역 크기 (히스토그램 제외한 뷰 영역)
        view = self.image_view.getView()
        view_rect = view.rect()
        view_w = view_rect.width()
        view_h = view_rect.height()

        if view_w <= 0 or view_h <= 0:
            # 아직 위젯 크기 안 잡혔을 때 기본값
            view.setRange(xRange=(0, img_w), yRange=(0, img_h), padding=0.02)
            return

        view_ratio = view_w / view_h

        if img_ratio > view_ratio:
            # 이미지가 뷰어보다 더 넓음 → 좌우를 꽉 채우고 상하 여백
            x_range = img_w
            y_range = img_w / view_ratio
        else:
            # 이미지가 뷰어보다 더 좁음 → 상하를 꽉 채우고 좌우 여백
            y_range = img_h
            x_range = img_h * view_ratio

        x_center = img_w / 2
        y_center = img_h / 2

        view.setLimits(
            xMin=-img_w * 0.05, xMax=img_w * 1.05,
            yMin=-img_h * 0.05, yMax=img_h * 1.05,
        )
        view.setRange(
            xRange=(x_center - x_range / 2, x_center + x_range / 2),
            yRange=(y_center - y_range / 2, y_center + y_range / 2),
            padding=0
        )

    def resizeEvent(self, event):
        """빰어 크기 변경 시 이미지 자동 fit (50ms 디바운싱)"""
        super().resizeEvent(event)
        if self._current_image is not None:
            self._resize_timer.start()

    def _on_resize_timeout(self):
        if self._current_image is not None:
            self._fit_to_view(self._current_image)

    # ─────────────────────────────────────────
    # ROI 모드
    # ─────────────────────────────────────────

    def _on_roi_mode_changed(self, mode_text: str):
        self._clear_roi_items()
        self._roi_line_pts = None
        self._roi_box_pts = None

        mode_map = {
            "Line Profile": 'line',
            "Box Profile":  'box',
            "X Profile":    'xprofile',
            "Y Profile":    'yprofile',
        }
        self._roi_mode = mode_map.get(mode_text, None)

        if self._roi_mode == 'xprofile':
            self._compute_x_profile()
        elif self._roi_mode == 'yprofile':
            self._compute_y_profile()

        vb = self.image_view.getView()
        if self._roi_mode in ('line', 'box'):
            vb.setCursor(Qt.CursorShape.CrossCursor)
        else:
            vb.setCursor(Qt.CursorShape.ArrowCursor)

    # ─────────────────────────────────────────
    # 마우스 드래그 ROI
    # ─────────────────────────────────────────

    def _on_mouse_drag(self, ev, axis=None):
        if self._roi_mode not in ('line', 'box'):
            self._orig_mouse_drag(ev, axis)
            return

        if self._current_image is None:
            self._orig_mouse_drag(ev, axis)
            return

        vb = self.image_view.getView()
        pos = vb.mapSceneToView(ev.scenePos())
        x, y = pos.x(), pos.y()

        if ev.isStart():
            self._draw_start = (x, y)
            self._clear_roi_items()
            ev.accept()

        elif ev.isFinish():
            if self._draw_start is not None:
                x0, y0 = self._draw_start
                self._finalize_roi(x0, y0, x, y)
            self._draw_start = None
            ev.accept()

        else:
            if self._draw_start is not None:
                x0, y0 = self._draw_start
                self._draw_preview(x0, y0, x, y)
            ev.accept()

    def _draw_preview(self, x0, y0, x1, y1):
        self._clear_roi_items()
        vb = self.image_view.getView()
        pen = pg.mkPen('#e94560', width=1.5, style=Qt.PenStyle.DashLine)

        if self._roi_mode == 'line':
            self._line_item = pg.PlotDataItem([x0, x1], [y0, y1], pen=pen)
        elif self._roi_mode == 'box':
            xs = [x0, x1, x1, x0, x0]
            ys = [y0, y0, y1, y1, y0]
            self._line_item = pg.PlotDataItem(xs, ys, pen=pen)

        if self._line_item is not None:
            vb.addItem(self._line_item)

    def _finalize_roi(self, x0, y0, x1, y1):
        self._clear_roi_items()
        vb = self.image_view.getView()
        pen = pg.mkPen('#e94560', width=2)

        if self._roi_mode == 'line':
            self._roi_line_pts = [(x0, y0), (x1, y1)]
            self._line_item = pg.PlotDataItem([x0, x1], [y0, y1], pen=pen)
            vb.addItem(self._line_item)
            self._compute_line_profile_from_pts(x0, y0, x1, y1)

        elif self._roi_mode == 'box':
            self._roi_box_pts = [(x0, y0), (x1, y1)]
            xs = [x0, x1, x1, x0, x0]
            ys = [y0, y0, y1, y1, y0]
            self._line_item = pg.PlotDataItem(xs, ys, pen=pen)
            vb.addItem(self._line_item)
            self._compute_box_profile_from_pts(x0, y0, x1, y1)

    # ─────────────────────────────────────────
    # 프로파일 계산
    # ─────────────────────────────────────────

    def _recompute_profile(self):
        if self._current_image is None:
            return
        if self._roi_mode == 'line' and self._roi_line_pts:
            (x0, y0), (x1, y1) = self._roi_line_pts
            self._compute_line_profile_from_pts(x0, y0, x1, y1)
        elif self._roi_mode == 'box' and self._roi_box_pts:
            (x0, y0), (x1, y1) = self._roi_box_pts
            self._compute_box_profile_from_pts(x0, y0, x1, y1)
        elif self._roi_mode == 'xprofile':
            self._compute_x_profile()
        elif self._roi_mode == 'yprofile':
            self._compute_y_profile()

    def _compute_line_profile_from_pts(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            from scipy import ndimage
            img = self._current_image.astype(np.float64)
            h, w = img.shape[:2]
            num = max(int(np.hypot(x1 - x0, y1 - y0)), 2)
            xs = np.linspace(np.clip(x0, 0, w - 1), np.clip(x1, 0, w - 1), num)
            ys = np.linspace(np.clip(y0, 0, h - 1), np.clip(y1, 0, h - 1), num)
            profile = ndimage.map_coordinates(img, [ys, xs], order=1)
            self.line_profile_updated.emit(profile, "Line")
        except Exception as e:
            print(f"Line profile error: {e}")

    def _compute_box_profile_from_pts(self, x0, y0, x1, y1):
        if self._current_image is None:
            return
        try:
            img = self._current_image
            h, w = img.shape[:2]
            ix0 = int(np.clip(min(x0, x1), 0, w - 1))
            ix1 = int(np.clip(max(x0, x1), 0, w - 1))
            iy0 = int(np.clip(min(y0, y1), 0, h - 1))
            iy1 = int(np.clip(max(y0, y1), 0, h - 1))
            if ix1 <= ix0 or iy1 <= iy0:
                return
            region = img[iy0:iy1, ix0:ix1].astype(np.float64)
            x_mean = region.mean(axis=0)
            y_mean = region.mean(axis=1)
            self.box_profile_updated.emit(x_mean, y_mean, "Box")
        except Exception as e:
            print(f"Box profile error: {e}")

    def _compute_x_profile(self):
        if self._current_image is None:
            return
        profile = self._current_image.mean(axis=0)
        self.line_profile_updated.emit(profile, "X Profile")

    def _compute_y_profile(self):
        if self._current_image is None:
            return
        profile = self._current_image.mean(axis=1)
        self.line_profile_updated.emit(profile, "Y Profile")

    # ─────────────────────────────────────────
    # ROI 아이템 정리
    # ─────────────────────────────────────────

    def _clear_roi_items(self):
        vb = self.image_view.getView()
        if self._line_item is not None:
            try:
                vb.removeItem(self._line_item)
            except Exception:
                pass
            self._line_item = None
        if self._box_item is not None:
            try:
                vb.removeItem(self._box_item)
            except Exception:
                pass
            self._box_item = None

    # ─────────────────────────────────────────
    # 컬러맵
    # ─────────────────────────────────────────

    def _on_cmap_changed(self, name: str):
        self._set_colormap(name)

    def _make_jet_colormap(self) -> pg.ColorMap:
        """jet 컬러맵 수동 생성"""
        import numpy as np
        pos = np.array([0.0, 0.125, 0.375, 0.625, 0.875, 1.0])
        color = np.array([
            [0,   0,   143, 255],
            [0,   0,   255, 255],
            [0,   255, 255, 255],
            [255, 255, 0,   255],
            [255, 0,   0,   255],
            [128, 0,   0,   255],
        ], dtype=np.ubyte)
        return pg.ColorMap(pos, color)

    def _set_colormap(self, name: str):
        try:
            cmap = pg.colormap.get(name, source='matplotlib')
            self.image_view.setColorMap(cmap)
        except Exception:
            try:
                cmap = pg.colormap.get(name)
                self.image_view.setColorMap(cmap)
            except Exception:
                # matplotlib 없을 때 jet 수동 생성
                if name == 'jet':
                    self.image_view.setColorMap(self._make_jet_colormap())

    # ─────────────────────────────────────────
    # 마우스 픽셀 정보
    # ─────────────────────────────────────────

    def _on_mouse_moved(self, pos):
        if self._current_image is None:
            return
        try:
            img_item = self.image_view.getImageItem()
            mouse_point = img_item.mapFromScene(pos)
            x, y = int(mouse_point.x()), int(mouse_point.y())
            h, w = self._current_image.shape[:2]
            if 0 <= x < w and 0 <= y < h:
                val = self._current_image[y, x]
                self.pixel_label.setText(f"X: {x}  Y: {y}  Val: {val:.1f}")
                self.pixel_info_updated.emit(x, y, float(val))
        except Exception:
            pass