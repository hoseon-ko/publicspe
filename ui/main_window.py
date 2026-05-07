"""
ui/main_window.py
통합 메인 윈도우 — 탭 기반 3-in-1 앱.

Tab 1: Live Control  — 실시간 카메라 + Picomotor 제어
Tab 2: Acquisition   — Picam 배치 획득 + SPE 저장
Tab 3: SPE Analysis  — SpeAnalyze 전체 기능
"""

from __future__ import annotations

from PyQt6.QtWidgets import QMainWindow, QTabWidget, QStatusBar, QLabel, QFrame
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QIcon

from ui.live.live_tab import LiveTab
from ui.acquisition.acquisition_tab import AcquisitionTab
from ui.analysis.analysis_tab import AnalysisTab
from ui.scan.scan_tab import ScanTab
from theme.styles import Fonts, Sizes, C_ACCENT, C_WARN, C_TEXT_DIM, C_BG_MED, C_BORDER


class MainWindow(QMainWindow):
    def __init__(self, spe_class=None):
        super().__init__()
        self._spe_class = spe_class
        self.setWindowTitle("SpeAnalyze — Integrated Lab Control")
        self.setMinimumSize(1300, 850)
        self.resize(1700, 1000)
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── 탭 위젯 ──────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #0f3460;
                background: #0a0f1e;
            }
            QTabBar::tab {
                background: #0f1729;
                color: #4a5a7a;
                padding: 8px 20px;
                font-family: 'Courier New';
                font-size: 12px;
                font-weight: bold;
                border: 1px solid #0f3460;
                letter-spacing: 1px;
            }
            QTabBar::tab:selected {
                background: #0a0f1e;
                color: #e94560;
                border-bottom: 2px solid #e94560;
            }
            QTabBar::tab:hover:!selected {
                color: #c0d0e0;
                background: #141f35;
            }
        """)
        self.setCentralWidget(self.tabs)

        # ── Tab 1: Live Control ───────────────────────────────────────
        self.live_tab = LiveTab()
        self.live_tab.status_message.connect(self._on_status)
        self.live_tab.camera_connected.connect(self._on_cam_connected)
        self.live_tab.camera_disconnected.connect(self._on_cam_disconnected)
        self.live_tab.cam_panel.exposure_applied.connect(self._on_exposure_changed)
        self.live_tab.frame_stats_updated.connect(self._on_frame_stats)
        self.tabs.addTab(self.live_tab, "📷  LIVE CONTROL")

        # ── Tab 2: Acquisition ────────────────────────────────────────
        self.acq_tab = AcquisitionTab()
        self.acq_tab.spe_saved.connect(self._on_spe_saved)
        self.acq_tab.log_message.connect(self._on_status)
        self.tabs.addTab(self.acq_tab, "🔬  ACQUISITION")

        # ── 카메라 공유: Live ↔ Acquisition ──────────────────────────
        # Live 탭에서 연결한 카메라를 Acquisition 탭이 그대로 사용
        self.live_tab.camera_connected.connect(self.acq_tab.set_shared_camera)
        self.live_tab.camera_disconnected.connect(self.acq_tab.clear_shared_camera)
        # Acquisition 시작 시 Live 스트림 자동 정지, 완료 시 재개
        self.acq_tab.acquisition_starting.connect(self.live_tab.stop_live)
        self.acq_tab.acquisition_done.connect(self.live_tab.resume_live)

        # ── Tab 3: Auto Scan ──────────────────────────────────────────
        self.scan_tab = ScanTab()
        self.scan_tab.log_message.connect(self._on_status)
        self.tabs.addTab(self.scan_tab, "🔄  AUTO SCAN")

        # 카메라 공유: Live ↔ Scan
        self.live_tab.camera_connected.connect(self.scan_tab.set_shared_camera)
        self.live_tab.camera_disconnected.connect(self.scan_tab.clear_shared_camera)
        self.scan_tab.scan_starting.connect(self.live_tab.stop_live)
        self.scan_tab.scan_done.connect(self.live_tab.resume_live)

        # 모터 패널 공유: Live 탭의 motor_panel → Scan 탭
        self.scan_tab.set_motor_panel(self.live_tab.motor_panel)

        # 노출 동기화 (Live ↔ Scan 양방향)
        self.live_tab.cam_panel.exposure_applied.connect(self.scan_tab.set_exposure_ui)
        self.scan_tab.exposure_changed.connect(self.live_tab.sync_exposure_ui)

        # 노출 동기화 (Live ↔ Acquisition 양방향)
        self.live_tab.cam_panel.exposure_applied.connect(self.acq_tab.set_exposure_ui)
        self.acq_tab.exposure_changed.connect(self.live_tab.sync_exposure_ui)

        # ── Tab 4: Analysis ───────────────────────────────────────────
        self.analysis_tab = AnalysisTab(spe_class=self._spe_class)
        self.analysis_tab.status_message.connect(self._on_status)
        self.tabs.addTab(self.analysis_tab, "📊  SPE ANALYSIS")

        # ── 상태바 ────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(False)
        self._status_bar.setStyleSheet(f"""
            QStatusBar {{
                background: {C_BG_MED};
                border-top: 1px solid {C_BORDER};
                color: {C_TEXT_DIM};
                font-family: '{Fonts.MONO}';
                font-size: {Sizes.SMALL};
            }}
            QStatusBar::item {{
                border: none;
            }}
        """)
        self.setStatusBar(self._status_bar)

        # 왼쪽: 일반 메시지
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}'; font-size: {Sizes.SMALL}; padding: 0 6px;"
        )
        self._status_bar.addWidget(self._status_label, 1)

        # 구분선 헬퍼
        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setStyleSheet(f"color: {C_BORDER}; margin: 3px 2px;")
            return f

        # 오른쪽 영구 위젯들 (카메라 | 노출 | 해상도 | FPS)
        _perm_style = (
            f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
            f" font-size: {Sizes.SMALL}; padding: 0 8px;"
        )
        _active_style = (
            f"color: {C_ACCENT}; font-family: '{Fonts.MONO}';"
            f" font-size: {Sizes.SMALL}; padding: 0 8px;"
        )

        self._sb_cam  = QLabel("📷 —")
        self._sb_exp  = QLabel("⏱ —")
        self._sb_size = QLabel("📐 —")
        self._sb_fps  = QLabel("fps: —")

        for lbl in (self._sb_cam, self._sb_exp, self._sb_size, self._sb_fps):
            lbl.setStyleSheet(_perm_style)

        self._sb_cam.setStyleSheet(_active_style)   # 카메라명은 강조색

        self._status_bar.addPermanentWidget(_sep())
        self._status_bar.addPermanentWidget(self._sb_cam)
        self._status_bar.addPermanentWidget(_sep())
        self._status_bar.addPermanentWidget(self._sb_exp)
        self._status_bar.addPermanentWidget(_sep())
        self._status_bar.addPermanentWidget(self._sb_size)
        self._status_bar.addPermanentWidget(_sep())
        self._status_bar.addPermanentWidget(self._sb_fps)
        self._status_bar.addPermanentWidget(_sep())

    # ── 슬롯 ─────────────────────────────────────────────────────────

    # ── 상태바 슬롯 ──────────────────────────────────────────────────

    def _on_cam_connected(self, cam):
        name = type(cam).__name__.replace("Camera", "")
        self._sb_cam.setText(f"📷 {name}")
        self._sb_cam.setStyleSheet(
            f"color: #4ecdc4; font-family: '{Fonts.MONO}';"
            f" font-size: {Sizes.SMALL}; padding: 0 8px;"
        )

    def _on_cam_disconnected(self):
        self._sb_cam.setText("📷 —")
        self._sb_cam.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
            f" font-size: {Sizes.SMALL}; padding: 0 8px;"
        )
        self._sb_size.setText("📐 —")
        self._sb_fps.setText("fps: —")
        self._sb_exp.setText("⏱ —")

    def _on_exposure_changed(self, ms: float):
        if ms < 1.0:
            self._sb_exp.setText(f"⏱ {ms*1000:.0f}µs")
        elif ms >= 1000.0:
            self._sb_exp.setText(f"⏱ {ms/1000:.2f}s")
        else:
            self._sb_exp.setText(f"⏱ {ms:.1f}ms")

    def _on_frame_stats(self, fps: float, w: int, h: int):
        self._sb_size.setText(f"📐 {w}×{h}")
        if fps > 0:
            self._sb_fps.setText(f"fps: {fps:.1f}")

    def _on_spe_saved(self, path: str):
        """Acquisition 탭에서 SPE 저장 완료 → Analysis 탭으로 자동 전달."""
        self.analysis_tab.open_spe(path)
        self.tabs.setCurrentWidget(self.analysis_tab)
        self._on_status(f"SPE 열림: {path}")

    def _on_status(self, msg: str):
        self._status_label.setText(msg)

    # ── 종료 처리 ────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.live_tab.cleanup()
        self.acq_tab.cleanup()
        self.scan_tab.cleanup()
        super().closeEvent(event)
