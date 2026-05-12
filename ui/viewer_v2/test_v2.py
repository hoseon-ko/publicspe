import sys
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, 
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from ui.viewer_v2.viewer_state import ViewerState
from ui.viewer_v2.viewer_main import SpeImageViewerV2

class TestV2Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpeAnalyze v2 Viewer Sync Test")
        self.resize(1200, 800)
        
        # 1. 전역 공유 상태 생성 (4개 탭이 공유할 뇌)
        self.shared_state = ViewerState()
        
        # 2. 메인 탭 위젯
        self.tabs = QTabWidget()
        
        # 테스트 컨트롤 영역
        ctrl_panel = QWidget()
        ctrl_layout = QHBoxLayout(ctrl_panel)
        btn_cmap = QPushButton("Cycle Colormap (SHARED)")
        btn_cmap.clicked.connect(self._cycle_colormap)
        ctrl_layout.addWidget(btn_cmap)
        
        btn_cross = QPushButton("Toggle Crosshair (SHARED)")
        btn_cross.clicked.connect(self._toggle_crosshair)
        ctrl_layout.addWidget(btn_cross)
        
        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.addWidget(ctrl_panel)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central_widget)
        
        # ── 탭 1~4: 공유 뷰어 ─────────────────────────────────────────
        for i in range(1, 5):
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(QLabel(f"SHARED VIEWER - TAB {i} (Synced with others)"))
            
            # 생성 시 shared_state를 주입
            viewer = SpeImageViewerV2(state=self.shared_state)
            layout.addWidget(viewer)
            
            # 테스트용 샘플 이미지 (Sync 탭은 동일한 데이터를 사용하여 동기화 확인)
            img = self._create_sample_image(1) 
            viewer.set_image(img)
            self.tabs.addTab(tab, f"Sync {i}")

        # ── 탭 5: 독립 뷰어 ──────────────────────────────────────────
        tab5 = QWidget()
        layout5 = QVBoxLayout(tab5)
        layout5.addWidget(QLabel("INDEPENDENT VIEWER - TAB 5 (Not synced)"))
        
        # state 인자를 주지 않으면 내부적으로 고유 상태 생성
        viewer5 = SpeImageViewerV2()
        layout5.addWidget(viewer5)
        
        img5 = self._create_sample_image(5)
        viewer5.set_image(img5)
        self.tabs.addTab(tab5, "Independent")

    def _cycle_colormap(self):
        cmaps = ['off', 'jet', 'viridis', 'hot', 'plasma']
        curr = self.shared_state.colormap
        nxt = cmaps[(cmaps.index(curr) + 1) % len(cmaps)]
        # vmin/vmax는 None으로 주면 자동 범위
        self.shared_state.update_colormap(nxt, None, None)

    def _toggle_crosshair(self):
        self.shared_state.toggle_crosshair()

    def _create_sample_image(self, seed: int):
        # 테스트용 노이즈 섞인 가우시안 빔 이미지 생성
        h, w = 512, 512
        yy, xx = np.mgrid[:h, :w]
        cx, cy = 256 + seed*20, 256 + seed*10
        sigma = 50
        img = np.exp(-((xx-cx)**2 + (yy-cy)**2) / (2*sigma**2))
        img = (img * 200 + np.random.rand(h, w) * 55).astype(np.uint8)
        return img

def excepthook(exc_type, exc_value, exc_tb):
    import traceback
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print("\n" + "="*60)
    print(" [CRITICAL ERROR DETECTED] ")
    print("="*60)
    print(msg)
    print("="*60 + "\n")

if __name__ == "__main__":
    sys.excepthook = excepthook
    
    app = QApplication(sys.argv)
    window = TestV2Window()
    window.show()
    sys.exit(app.exec())
