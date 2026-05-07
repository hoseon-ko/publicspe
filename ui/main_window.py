"""
ui/main_window.py
통합 메인 윈도우 — LightField 스타일.

레이아웃:
  ┌─ 헤더 바 (28px) ─────────────────────────────────────────────┐
  │  SpeAnalyze  │ Live │ Acquire │ Scan │ Analysis │     status │
  ├──────────────────────────────────────────────────────────────┤
  │                                                              │
  │   각 모드 콘텐츠 (QStackedWidget)                            │
  │                                                              │
  └──────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QStackedWidget, QStatusBar,
    QLabel, QFrame, QVBoxLayout, QHBoxLayout, QPushButton,
)
from PyQt6.QtCore import Qt, QSize, QSettings
from PyQt6.QtGui import QFont

from ui.live.live_tab import LiveTab
from ui.acquisition.acquisition_tab import AcquisitionTab
from ui.analysis.analysis_tab import AnalysisTab
from ui.scan.scan_tab import ScanTab
from ui.autofocus.autofocus_tab import AutoFocusTab
from theme.styles import Fonts, Sizes, C_ACCENT, C_TEXT_DIM, C_BG_MED, C_BORDER


# ── 헤더 바 색상 (LightField 다크 헤더) ──────────────────────────
_HDR_BG      = "#161b27"   # 헤더 배경
_HDR_BORDER  = "#1e2a3e"   # 헤더 하단 구분선
_TAB_NORMAL  = "#4a5a70"   # 비선택 탭 텍스트
_TAB_HOVER   = "#8aa0bc"   # 호버
_TAB_ACTIVE  = "#e0e8f0"   # 선택된 탭 텍스트
_TAB_LINE    = C_ACCENT    # 선택 탭 하단 강조선 색


class MainWindow(QMainWindow):
    def __init__(self, spe_class=None):
        super().__init__()
        self._spe_class = spe_class
        self.setWindowTitle("SpeAnalyze — Integrated Lab Control")
        self.setMinimumSize(1300, 850)
        self.resize(1700, 1000)
        self._build_ui()
        self._restore_settings()

    # ── UI ───────────────────────────────────────────────────────────

    def _build_ui(self):
        _FC = Fonts.MONO

        # ── 루트: VBox → [헤더 바, 콘텐츠 스택] ─────────────────────
        root = QWidget()
        root.setObjectName("root")
        root.setStyleSheet("QWidget#root { background: #080e1e; }")
        root_v = QVBoxLayout(root)
        root_v.setContentsMargins(0, 0, 0, 0)
        root_v.setSpacing(0)
        self.setCentralWidget(root)

        # ── 헤더 바 (LightField 스타일) ──────────────────────────────
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(30)
        header.setStyleSheet(f"""
            QWidget#header {{
                background: {_HDR_BG};
                border-bottom: 1px solid {_HDR_BORDER};
            }}
        """)
        hdr_h = QHBoxLayout(header)
        hdr_h.setContentsMargins(8, 0, 8, 0)
        hdr_h.setSpacing(0)

        # 앱 이름 레이블
        lbl_app = QLabel("SpeAnalyze")
        lbl_app.setStyleSheet(
            f"color: #5a7a9a; font-family: '{_FC}'; font-size: 11px;"
            " font-weight: bold; letter-spacing: 2px;"
            " padding: 0 14px 0 4px;"
            f" border-right: 1px solid {_HDR_BORDER};"
        )
        hdr_h.addWidget(lbl_app)

        # 구분 간격
        hdr_h.addSpacing(8)

        # ── 모드 탭 버튼 (LightField 상단 텍스트 탭 스타일) ──────────
        _tab_qss = f"""
            QPushButton {{
                background: transparent;
                color: {_TAB_NORMAL};
                border: none;
                border-bottom: 2px solid transparent;
                font-family: '{_FC}';
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 0 16px;
                height: 30px;
            }}
            QPushButton:hover {{
                color: {_TAB_HOVER};
                background: rgba(255,255,255,0.03);
            }}
            QPushButton:checked {{
                color: {_TAB_ACTIVE};
                border-bottom: 2px solid {_TAB_LINE};
            }}
        """

        self._nav_btns: list[QPushButton] = []

        # ── 콘텐츠 스택 ──────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: #0a0f1e;")

        # ── 모드별 탭 인스턴스 생성 ─────────────────────────────────
        self.live_tab = LiveTab()
        self.live_tab.status_message.connect(self._on_status)
        self.live_tab.camera_connected.connect(self._on_cam_connected)
        self.live_tab.camera_disconnected.connect(self._on_cam_disconnected)
        self.live_tab.cam_panel.exposure_applied.connect(self._on_exposure_changed)
        self.live_tab.frame_stats_updated.connect(self._on_frame_stats)

        self.acq_tab = AcquisitionTab()
        self.acq_tab.spe_saved.connect(self._on_spe_saved)
        self.acq_tab.log_message.connect(self._on_status)

        self.scan_tab = ScanTab()
        self.scan_tab.log_message.connect(self._on_status)

        self.analysis_tab = AnalysisTab(spe_class=self._spe_class)
        self.analysis_tab.status_message.connect(self._on_status)

        self.af_tab = AutoFocusTab()
        self.af_tab.log_message.connect(self._on_status)
        self.af_tab.af_starting.connect(self.live_tab.stop_live)
        self.af_tab.af_done.connect(self.live_tab.resume_live)

        # 탭 버튼 + 스택 등록
        _modes = [
            ("Live",      self.live_tab),
            ("Acquire",   self.acq_tab),
            ("Scan",      self.scan_tab),
            ("AutoFocus", self.af_tab),
            ("Analysis",  self.analysis_tab),
        ]
        for idx, (label, widget) in enumerate(_modes):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setFixedHeight(30)
            btn.setStyleSheet(_tab_qss)
            btn.clicked.connect(lambda checked, i=idx: self._switch_mode(i))
            hdr_h.addWidget(btn)
            self._nav_btns.append(btn)
            self.stack.addWidget(widget)

        self._nav_btns[0].setChecked(True)   # Live 기본 선택

        # 헤더 오른쪽: 상태 위젯들
        hdr_h.addStretch(1)

        def _vsep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedHeight(16)
            f.setStyleSheet(f"color: {_HDR_BORDER}; margin: 0 4px;")
            return f

        _hdr_lbl_style = (
            f"color: {_TAB_NORMAL}; font-family: '{_FC}';"
            " font-size: 10px; padding: 0 6px;"
        )
        self._hdr_cam  = QLabel("—")
        self._hdr_exp  = QLabel("—")
        self._hdr_fps  = QLabel("—")
        for lbl in (self._hdr_cam, self._hdr_exp, self._hdr_fps):
            lbl.setStyleSheet(_hdr_lbl_style)

        hdr_h.addWidget(_vsep())
        hdr_h.addWidget(self._hdr_cam)
        hdr_h.addWidget(_vsep())
        hdr_h.addWidget(self._hdr_exp)
        hdr_h.addWidget(_vsep())
        hdr_h.addWidget(self._hdr_fps)
        hdr_h.addSpacing(4)

        root_v.addWidget(header)
        root_v.addWidget(self.stack, 1)

        # ── 인터탭 연결 ──────────────────────────────────────────────
        self.live_tab.camera_connected.connect(self.acq_tab.set_shared_camera)
        self.live_tab.camera_disconnected.connect(self.acq_tab.clear_shared_camera)
        self.acq_tab.acquisition_starting.connect(self.live_tab.stop_live)
        self.acq_tab.acquisition_done.connect(self.live_tab.resume_live)

        self.live_tab.camera_connected.connect(self.scan_tab.set_shared_camera)
        self.live_tab.camera_disconnected.connect(self.scan_tab.clear_shared_camera)
        self.scan_tab.scan_starting.connect(self.live_tab.stop_live)
        self.scan_tab.scan_done.connect(self.live_tab.resume_live)

        # 카메라 공유: Live ↔ AutoFocus
        self.live_tab.camera_connected.connect(self.af_tab.set_shared_camera)
        self.live_tab.camera_disconnected.connect(self.af_tab.clear_shared_camera)

        # KIMM 공유: Live ↔ AutoFocus
        self.live_tab.kimm_z_panel.kimm_connected.connect(self.af_tab.set_kimm_ctrl)
        self.live_tab.kimm_z_panel.kimm_disconnected.connect(self.af_tab.clear_kimm_ctrl)

        self.scan_tab.set_motor_panel(self.live_tab.motor_panel)

        self.live_tab.cam_panel.exposure_applied.connect(self.scan_tab.set_exposure_ui)
        self.scan_tab.exposure_changed.connect(self.live_tab.sync_exposure_ui)
        self.live_tab.cam_panel.exposure_applied.connect(self.acq_tab.set_exposure_ui)
        self.acq_tab.exposure_changed.connect(self.live_tab.sync_exposure_ui)

        # ── 상태바 (하단) ─────────────────────────────────────────────
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
            QStatusBar::item {{ border: none; }}
        """)
        self.setStatusBar(self._status_bar)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(
            f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
            f" font-size: {Sizes.SMALL}; padding: 0 6px;"
        )
        self._status_bar.addWidget(self._status_label, 1)

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setStyleSheet(f"color: {C_BORDER}; margin: 3px 2px;")
            return f

        _perm = f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}'; font-size: {Sizes.SMALL}; padding: 0 8px;"
        self._sb_cam  = QLabel("📷 —")
        self._sb_exp  = QLabel("⏱ —")
        self._sb_size = QLabel("📐 —")
        self._sb_fps  = QLabel("fps: —")
        for lbl in (self._sb_cam, self._sb_exp, self._sb_size, self._sb_fps):
            lbl.setStyleSheet(_perm)
        self._sb_cam.setStyleSheet(
            f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: {Sizes.SMALL}; padding: 0 8px;"
        )

        for w in (_sep(), self._sb_cam, _sep(), self._sb_exp,
                  _sep(), self._sb_size, _sep(), self._sb_fps, _sep()):
            self._status_bar.addPermanentWidget(w)

    # ── 모드 전환 ────────────────────────────────────────────────────

    def _switch_mode(self, idx: int):
        # Hide range popups from all viewers when switching tabs
        for i in range(self.stack.count()):
            tab = self.stack.widget(i)
            # Check common viewer attribute names across different tabs
            for attr in ('image_viewer', 'viewer', 'preview_viewer'):
                viewer = getattr(tab, attr, None)
                if viewer and hasattr(viewer, 'hide_range_popup'):
                    viewer.hide_range_popup()

        self.stack.setCurrentIndex(idx)

        # 활성화된 탭에 알림 (특수 동작 수행용)
        active_tab = self.stack.widget(idx)
        if hasattr(active_tab, "on_tab_activated"):
            try:
                active_tab.on_tab_activated()
            except Exception as e:
                print(f"Error in on_tab_activated for {type(active_tab).__name__}: {e}")

        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)

    # ── 슬롯 ─────────────────────────────────────────────────────────

    def _on_cam_connected(self, cam):
        name = type(cam).__name__.replace("Camera", "")
        _s = (f"color: #4ecdc4; font-family: '{Fonts.MONO}';"
              f" font-size: {Sizes.SMALL}; padding: 0 8px;")
        self._sb_cam.setText(f"📷 {name}")
        self._sb_cam.setStyleSheet(_s)
        self._hdr_cam.setText(name)
        self._hdr_cam.setStyleSheet(
            f"color: #4ecdc4; font-family: '{Fonts.MONO}'; font-size: 10px; padding: 0 6px;"
        )

    def _on_cam_disconnected(self):
        _s = (f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
              f" font-size: {Sizes.SMALL}; padding: 0 8px;")
        self._sb_cam.setText("📷 —"); self._sb_cam.setStyleSheet(
            f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: {Sizes.SMALL}; padding: 0 8px;"
        )
        self._sb_size.setText("📐 —")
        self._sb_fps.setText("fps: —")
        self._sb_exp.setText("⏱ —")
        _ds = (f"color: {_TAB_NORMAL}; font-family: '{Fonts.MONO}'; font-size: 10px; padding: 0 6px;")
        self._hdr_cam.setText("—"); self._hdr_cam.setStyleSheet(_ds)
        self._hdr_exp.setText("—"); self._hdr_fps.setText("—")

    def _on_exposure_changed(self, ms: float):
        if ms < 1.0:
            t = f"{ms*1000:.0f}µs"
        elif ms >= 1000.0:
            t = f"{ms/1000:.2f}s"
        else:
            t = f"{ms:.1f}ms"
        self._sb_exp.setText(f"⏱ {t}")
        self._hdr_exp.setText(t)

    def _on_frame_stats(self, fps: float, w: int, h: int):
        self._sb_size.setText(f"📐 {w}×{h}")
        if fps > 0:
            fps_t = f"{fps:.1f}fps"
            self._sb_fps.setText(f"fps: {fps:.1f}")
            self._hdr_fps.setText(fps_t)

    def _on_spe_saved(self, path: str):
        self.analysis_tab.open_spe(path)
        self._switch_mode(4)   # Analysis = index 4
        self._on_status(f"SPE 열림: {path}")

    def _on_status(self, msg: str):
        self._status_label.setText(msg)

    # ── 종료 처리 ────────────────────────────────────────────────────

    # ── 종료 처리 ────────────────────────────────────────────────────

    def save_all_settings(self):
        """aboutToQuit / SIGINT 등 모든 종료 경로에서 설정 저장 및 UI 상태 저장."""
        # 1. 서브 탭 하드웨어/워커 정리 (먼저 수행)
        self.live_tab.cleanup()
        self.acq_tab.cleanup()
        self.scan_tab.cleanup()
        self.af_tab.cleanup()

        # 2. MainWindow 상태 저장
        s = QSettings("SpeAnalyze", "MainWindow")
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        s.setValue("active_tab", self.stack.currentIndex())

        # 3. 모든 서브 탭 설정 저장
        for tab in (self.live_tab, self.acq_tab, self.scan_tab, self.af_tab, self.analysis_tab):
            if hasattr(tab, "_save_settings"):
                try:
                    tab._save_settings()
                except Exception as e:
                    print(f"Error saving settings for {type(tab).__name__}: {e}")

    def _restore_settings(self):
        """Load saved MainWindow UI state and forward to sub‑tabs."""
        s = QSettings("SpeAnalyze", "MainWindow")
        try:
            geom = s.value("geometry")
            if geom:
                self.restoreGeometry(geom)
            state = s.value("windowState")
            if state:
                self.restoreState(state)
            idx = s.value("active_tab")
            if idx is not None:
                idx = int(idx)
                if 0 <= idx < self.stack.count():
                    self._switch_mode(idx)
        except Exception as e:
            print(f"MainWindow settings restore error: {e}")

        # Restore sub‑tab settings
        for tab in (self.live_tab, self.acq_tab, self.scan_tab, self.af_tab, self.analysis_tab):
            if hasattr(tab, "_restore_settings"):
                try:
                    tab._restore_settings()
                except Exception as e:
                    print(f"Error restoring settings for {type(tab).__name__}: {e}")

    def closeEvent(self, event):
        self.save_all_settings()
        super().closeEvent(event)
