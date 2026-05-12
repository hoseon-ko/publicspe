from __future__ import annotations
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from ui.viewer_v2.ruler_widgets import RulerWidgetV2
from ui.viewer_v2.viewer_state import ViewerState

class RulerSystem(QObject):
    """
    설계안 2번: 눈금자 및 프로파일 관리 계층.
    X, Y 두 개의 RulerWidget을 관리하고 데이터를 분배합니다.
    """
    layout_size_changed = pyqtSignal(int, int) # Y_width, X_height

    def __init__(self, state: ViewerState, viewer_main):
        super().__init__()
        self._state = state
        self._main = viewer_main
        
        # 뷰어가 먼저 생성된 후 룰러가 생성되어야 함
        self.ruler_x = RulerWidgetV2(state, viewer_main.view, 'horizontal', viewer_main)
        self.ruler_y = RulerWidgetV2(state, viewer_main.view, 'vertical', viewer_main)
        
        # 사이즈 변경 시그널 연결 (버그 해결을 위해 통합 시그널로 방출)
        self.ruler_x.size_changed.connect(self._on_ruler_resized)
        self.ruler_y.size_changed.connect(self._on_ruler_resized)

    def _on_ruler_resized(self, _):
        # 현재 두 룰러의 사이즈를 측정해서 보냄
        self.layout_size_changed.emit(self.ruler_y.width(), self.ruler_x.height())

    def set_profiles(self, x_data: np.ndarray, y_data: np.ndarray):
        """설계안대로 외부에서 데이터를 주면 각 룰러에 전달"""
        self._state.update_profiles(x_data, y_data)
