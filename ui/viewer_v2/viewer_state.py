from __future__ import annotations
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, QRectF
from typing import Optional

class ViewerState(QObject):
    """
    뷰어의 모든 상태(줌, 팬, 컬러맵, ROI)를 관리하는 클래스.
    모든 주요 필드를 프로퍼티로 구현하여 값 변경 시 자동으로 시그널을 방출합니다.
    """
    # 상태 변경 시 발생하는 시그널들
    view_transformed = pyqtSignal(float, float, float)  # scale, x_offset, y_offset
    colormap_changed = pyqtSignal(str)                  # cmap_name
    image_size_changed = pyqtSignal(int, int)           # width, height
    crosshair_moved = pyqtSignal(float, float)          # x, y
    roi_updated = pyqtSignal(QRectF)                    # selected rect
    crosshair_visible_changed = pyqtSignal(bool)        # on/off
    range_changed = pyqtSignal(float, float)            # vmin, vmax
    profile_data_changed = pyqtSignal()                 # profile data updated

    def __init__(self):
        super().__init__()
        # 내부 저장 필드
        self._scale: float = 1.0
        self._x_offset: float = 0.0
        self._y_offset: float = 0.0
        
        self._colormap: str = 'off'
        self._vmin: float = 0.0
        self._vmax: float = 255.0
        
        self.img_width: int = 0
        self.img_height: int = 0
        self.bit_depth: int = 8
        
        self.crosshair_x: float = 0.0
        self.crosshair_y: float = 0.0
        self.crosshair_visible: bool = True
        self.selected_roi: QRectF = QRectF()
        
        self._x_profile: Optional[np.ndarray] = None
        self._y_profile: Optional[np.ndarray] = None

    # --- Property: scale, offsets ---
    @property
    def scale(self): return self._scale
    @property
    def x_offset(self): return self._x_offset
    @property
    def y_offset(self): return self._y_offset

    def update_transform(self, scale: float, x_off: float, y_off: float):
        if self._scale == scale and self._x_offset == x_off and self._y_offset == y_off:
            return
        self._scale = scale
        self._x_offset = x_off
        self._y_offset = y_off
        self.view_transformed.emit(scale, x_off, y_off)

    # --- Property: colormap ---
    @property
    def colormap(self): return self._colormap
    @colormap.setter
    def colormap(self, val: str):
        if self._colormap != val:
            self._colormap = val
            print(f"[ViewerState] Colormap changed: {val}")
            self.colormap_changed.emit(val)

    # --- Property: vmin, vmax ---
    @property
    def vmin(self): return self._vmin
    @property
    def vmax(self): return self._vmax

    def update_range(self, vmin: float, vmax: float):
        if self._vmin == vmin and self._vmax == vmax:
            return
        self._vmin = vmin
        self._vmax = vmax
        print(f"[ViewerState] Range changed: {vmin:.1f} ~ {vmax:.1f}")
        self.range_changed.emit(vmin, vmax)
        # 범위가 바뀌면 화면도 다시 그려야 하므로 colormap_changed도 같이 쏴줌
        self.colormap_changed.emit(self._colormap)

    def update_colormap(self, name: str, vmin: Optional[float] = None, vmax: Optional[float] = None):
        """test_v2.py 호환용 헬퍼"""
        if vmin is not None and vmax is not None:
            self.update_range(vmin, vmax)
        self.colormap = name

    # --- Property: profiles ---
    @property
    def x_profile(self): return self._x_profile
    @x_profile.setter
    def x_profile(self, val):
        self._x_profile = val
        self.profile_data_changed.emit()

    @property
    def y_profile(self): return self._y_profile
    @y_profile.setter
    def y_profile(self, val):
        self._y_profile = val
        self.profile_data_changed.emit()

    def update_profiles(self, x_prof: np.ndarray, y_prof: np.ndarray):
        """기존 코드 호환성을 위한 프로파일 일괄 업데이트"""
        self._x_profile = x_prof
        self._y_profile = y_prof
        self.profile_data_changed.emit()

    # --- Interaction Helpers ---
    def update_crosshair(self, x: float, y: float):
        self.crosshair_x = x
        self.crosshair_y = y
        self.crosshair_visible = True
        self.crosshair_moved.emit(x, y)
        self.crosshair_visible_changed.emit(True)

    def toggle_crosshair(self, visible: Optional[bool] = None):
        if visible is None:
            self.crosshair_visible = not self.crosshair_visible
        else:
            self.crosshair_visible = visible
        self.crosshair_visible_changed.emit(self.crosshair_visible)

    def update_roi(self, rect: QRectF):
        self.selected_roi = rect
        self.roi_updated.emit(rect)
