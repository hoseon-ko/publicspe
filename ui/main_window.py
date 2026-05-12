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
    QDockWidget, QTextEdit, QTabWidget, QApplication,
)
from PyQt6.QtCore import Qt, QSize, QSettings
from PyQt6.QtGui import QFont, QShortcut, QKeySequence, QPixmap, QPainter

from ui.live.live_tab import LiveTab
from ui.acquisition.acquisition_tab import AcquisitionTab
from ui.analysis.analysis_tab import AnalysisTab
from ui.scan.scan_tab import ScanTab
from ui.autofocus.autofocus_tab import AutoFocusTab
from ui.kinematic.kinematic_tab import KinematicTab
from ui.deepalign.deepalign_main_tab import DeepAlignMainTab
from core.hal.adapters import HikvisionCameraAdapter, PicamCameraAdapter, SimulatedCameraAdapter
from core.motor.acs_stage import AcsStageController
from core.session.device_session_hub import DeviceSessionHub
from theme.styles import Fonts, Sizes, C_ACCENT, C_TEXT_DIM, C_BG_MED, C_BORDER
from core.logger import app_logger, register_ui_callback
from ui.bridge.hub_bindings import bind_status_to_main_window


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
        self.acs_ctrl = AcsStageController()
        self.session_hub = DeviceSessionHub(self)
        self.session_hub.register_camera_hal("hikvision", HikvisionCameraAdapter)
        self.session_hub.register_camera_hal("picam", PicamCameraAdapter)
        self.session_hub.register_camera_hal("simulated", SimulatedCameraAdapter)
        self.session_hub.select_camera_vendor("simulated")
        self.setWindowTitle("SpeAnalyze — Integrated Lab Control")
        self.setMinimumSize(1300, 850)
        self.resize(1700, 1000)
        self._setup_log_dock()
        register_ui_callback(self._log)
        self._build_ui()
        self._restore_settings()
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        # UI 디버깅용 스크린샷 덤프 (Ctrl+Alt+S)
        self.sc_dump = QShortcut(QKeySequence("Ctrl+Alt+S"), self)
        self.sc_dump.activated.connect(self.dump_ui_screenshots)

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
        header.setFixedHeight(46)
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
            f"color: #4ecdc4; font-family: '{_FC}', 'Segoe UI'; font-size: 15px;"
            " font-weight: bold; letter-spacing: 2px;"
            " padding: 0 20px 0 10px;"
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
                border-bottom: 3px solid transparent;
                font-family: 'Segoe UI', '{_FC}';
                font-size: 13px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 0 20px;
                height: 46px;
            }}
            QPushButton:hover {{
                color: #ffffff;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.06), stop:1 transparent);
            }}
            QPushButton:checked {{
                color: #ffffff;
                border-bottom: 3px solid {_TAB_LINE};
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(78, 205, 196, 0.15), stop:1 transparent);
            }}
        """

        self._nav_btns: list[QPushButton] = []

        # ── 콘텐츠 스택 ──────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: #0a0f1e;")

        # ── 모드별 탭 인스턴스 생성 ─────────────────────────────────
        self.live_tab = LiveTab(acs_ctrl=self.acs_ctrl)
        self.live_tab.status_message.connect(self._on_status)
        self.live_tab.camera_connected.connect(self._on_camera_connected)
        self.live_tab.camera_disconnected.connect(self._on_camera_disconnected)
        self.live_tab.camera_panel.exposure_applied.connect(self._on_exposure_changed)
        self.live_tab.frame_stats_updated.connect(self._on_frame_stats)
        bind_status_to_main_window(self.session_hub, self)
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

        self.kin_tab = KinematicTab(acs_ctrl=self.acs_ctrl)
        self.kin_tab.log_message.connect(self._on_status)
        self.kin_tab.kin_starting.connect(self.live_tab.stop_live)
        self.kin_tab.kin_done.connect(self.live_tab.resume_live)

        self.deep_align_tab = DeepAlignMainTab()
        self.deep_align_tab.bind_session_hub(self.session_hub)
        # 탭 버튼 + 스택 등록
        _modes = [
            ("🌌 DeepAlign", self.deep_align_tab),
            ("📷 Live",      self.live_tab),
            ("📥 Acquire",   self.acq_tab),
            ("🔬 Scan",      self.scan_tab),
            ("🎯 AutoFocus", self.af_tab),
            ("📐 Kinematic", self.kin_tab),
            ("📊 Analysis",  self.analysis_tab),
        ]
        for idx, (label, widget) in enumerate(_modes):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setFixedHeight(46)
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
        self._hdr_camera  = QLabel("—")
        self._hdr_exp  = QLabel("—")
        self._hdr_fps  = QLabel("—")
        for lbl in (self._hdr_camera, self._hdr_exp, self._hdr_fps):
            lbl.setStyleSheet(_hdr_lbl_style)

        hdr_h.addWidget(_vsep())
        hdr_h.addWidget(self._hdr_camera)
        hdr_h.addWidget(_vsep())
        hdr_h.addWidget(self._hdr_exp)
        hdr_h.addWidget(_vsep())
        hdr_h.addWidget(self._hdr_fps)
        hdr_h.addSpacing(4)

        root_v.addWidget(header)
        root_v.addWidget(self.stack, 1)

        # ── 인터탭 연결 ──────────────────────────────────────────────
        self.live_tab.camera_connected.connect(self.acq_tab.set_shared_cameraera)
        self.live_tab.camera_disconnected.connect(self.acq_tab.clear_shared_cameraera)
        self.acq_tab.acquisition_starting.connect(self.live_tab.stop_live)
        self.acq_tab.acquisition_done.connect(self.live_tab.resume_live)

        self.live_tab.camera_connected.connect(self.scan_tab.set_shared_cameraera)
        self.live_tab.camera_disconnected.connect(self.scan_tab.clear_shared_cameraera)
        self.scan_tab.scan_starting.connect(self.live_tab.stop_live)
        self.scan_tab.scan_done.connect(self.live_tab.resume_live)

        # 카메라 공유: Live ↔ AutoFocus
        self.live_tab.camera_connected.connect(self.af_tab.set_shared_cameraera)
        self.live_tab.camera_disconnected.connect(self.af_tab.clear_shared_cameraera)

        # KIMM 공유: Live ↔ AutoFocus
        self.live_tab.kimm_z_panel.kimm_connected.connect(self.af_tab.set_kimm_ctrl)
        self.live_tab.kimm_z_panel.kimm_disconnected.connect(self.af_tab.clear_kimm_ctrl)

        # 카메라 공유: Live ↔ Kinematic
        self.live_tab.camera_connected.connect(self.kin_tab.set_shared_cameraera)
        self.live_tab.camera_disconnected.connect(self.kin_tab.clear_shared_cameraera)

        # ACS 스테이지 공유: Live ↔ Kinematic
        self.live_tab.acs_stage_panel.acs_connected.connect(self.kin_tab.set_acs_ctrl)
        self.live_tab.acs_stage_panel.acs_disconnected.connect(self.kin_tab.clear_acs_ctrl)

        # Kinematic 탭에서 직접 연결한 경우 Live 탭에도 전파
        self.kin_tab.acs_panel.acs_connected.connect(self.live_tab.acs_stage_panel.set_controller)
        self.kin_tab.acs_panel.acs_disconnected.connect(lambda: self.live_tab.acs_stage_panel.set_controller(None))

        # 하드웨어 공유: Live ↔ DeepAlign
        self.live_tab.kimm_z_panel.kimm_connected.connect(self.deep_align_tab.set_kimm_ctrl)
        self.live_tab.kimm_z_panel.kimm_disconnected.connect(self.deep_align_tab.clear_kimm_ctrl)
        self.live_tab.acs_stage_panel.acs_connected.connect(self.deep_align_tab.set_acs_ctrl)
        self.live_tab.acs_stage_panel.acs_disconnected.connect(self.deep_align_tab.clear_acs_ctrl)
        self.live_tab.motor_panel.connected.connect(self.deep_align_tab.set_picos_ctrl)

        self.scan_tab.set_motor_panel(self.live_tab.motor_panel)

        self.live_tab.camera_panel.exposure_applied.connect(self.scan_tab.set_exposure_ui)
        self.scan_tab.exposure_changed.connect(self.live_tab.sync_exposure_ui)
        self.live_tab.camera_panel.exposure_applied.connect(self.acq_tab.set_exposure_ui)
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
        self._sb_camera  = QLabel("📷 —")
        self._sb_exp  = QLabel("⏱ —")
        self._sb_size = QLabel("📐 —")
        self._sb_fps  = QLabel("fps: —")
        for lbl in (self._sb_camera, self._sb_exp, self._sb_size, self._sb_fps):
            lbl.setStyleSheet(_perm)
        self._sb_camera.setStyleSheet(
            f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: {Sizes.SMALL}; padding: 0 8px;"
        )

        for w in (_sep(), self._sb_camera, _sep(), self._sb_exp,
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

        # #SYNC: 특정 탭 진입 시 Live 탭의 마지막 이미지를 가져와서 뷰어에 표시 (ROI 등 편의성)
        if active_tab in (self.af_tab, getattr(self, 'kin_tab', None), self.acq_tab):
            last_raw = self.live_tab.get_last_raw()
            if last_raw is not None:
                viewer = None
                if active_tab == self.af_tab:
                    viewer = getattr(self.af_tab, "image_viewer", None)
                elif active_tab == getattr(self, "kin_tab", None):
                    viewer = getattr(self.kin_tab, "image_viewer", None)
                elif active_tab == self.acq_tab:
                    viewer = getattr(self.acq_tab, "preview_viewer", None)
                
                if viewer is not None and hasattr(viewer, 'set_source_image'):
                    viewer.set_source_image(last_raw)

        if hasattr(active_tab, "on_tab_activated"):
            try:
                active_tab.on_tab_activated()
            except Exception as e:
                app_logger.error(f"Error in on_tab_activated for {type(active_tab).__name__}: {e}", exc_info=True)

        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)

    def dump_ui_screenshots(self):
        """모든 탭을 순회하며 스크린샷을 찍어 'UI_Debug' 폴더에 저장한다."""
        import os
        from datetime import datetime
        
        save_dir = "UI_Debug"
        os.makedirs(save_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        orig_idx = self.stack.currentIndex()
        
        try:
            for i in range(self.stack.count()):
                self._switch_mode(i)
                QApplication.processEvents() # UI 갱신 대기
                
                tab = self.stack.widget(i)
                label = self._nav_btns[i].text().strip()
                filename = f"tab_{i}_{label}_{ts}.png"
                path = os.path.join(save_dir, filename)
                
                # 메인 윈도우 전체 캡처
                pixmap = self.grab()
                pixmap.save(path, "PNG")
                app_logger.info(f"[UI Dump] Saved: {path}")
                
            self._switch_mode(orig_idx)
            self._on_status(f"📸 모든 탭 스크린샷 저장 완료 ({save_dir})")
        except Exception as e:
            self._on_status(f"❌ 스크린샷 저장 실패: {e}")

    # ── 슬롯 ─────────────────────────────────────────────────────────

    def _on_camera_connected(self, camera):
        name = type(camera).__name__.replace("Camera", "")
        _s = (f"color: #4ecdc4; font-family: '{Fonts.MONO}';"
              f" font-size: {Sizes.SMALL}; padding: 0 8px;")
        self._sb_camera.setText(f"📷 {name}")
        self._sb_camera.setStyleSheet(_s)
        self._hdr_camera.setText(name)
        self._hdr_camera.setStyleSheet(
            f"color: #4ecdc4; font-family: '{Fonts.MONO}'; font-size: 10px; padding: 0 6px;"
        )

    def _on_camera_disconnected(self):
        _s = (f"color: {C_TEXT_DIM}; font-family: '{Fonts.MONO}';"
              f" font-size: {Sizes.SMALL}; padding: 0 8px;")
        self._sb_camera.setText("📷 —"); self._sb_camera.setStyleSheet(
            f"color: {C_ACCENT}; font-family: '{Fonts.MONO}'; font-size: {Sizes.SMALL}; padding: 0 8px;"
        )
        self._sb_size.setText("📐 —")
        self._sb_fps.setText("fps: —")
        self._sb_exp.setText("⏱ —")
        _ds = (f"color: {_TAB_NORMAL}; font-family: '{Fonts.MONO}'; font-size: 10px; padding: 0 6px;")
        self._hdr_camera.setText("—"); self._hdr_camera.setStyleSheet(_ds)
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
        analysis_idx = self.stack.indexOf(self.analysis_tab)
        if analysis_idx >= 0:
            self._switch_mode(analysis_idx)
        self._on_status(f"SPE 열림: {path}")

    def _on_status(self, msg: str):
        self._status_label.setText(msg)

    # ── 종료 처리 ────────────────────────────────────────────────────

    # ── 종료 처리 ────────────────────────────────────────────────────

    def save_all_settings(self):
        """aboutToQuit / SIGINT 등 모든 종료 경로에서 설정 저장 및 UI 상태 저장."""
        # 1. MainWindow 상태 저장
        s = QSettings("SpeAnalyze", "MainWindow")
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        s.setValue("active_tab", self.stack.currentIndex())

        # 2. 서브 탭 설정 저장 및 하드웨어/워커 정리
        for tab in (self.live_tab, self.acq_tab, self.scan_tab, self.af_tab, self.kin_tab, self.analysis_tab, self.deep_align_tab):
            if hasattr(tab, "_save_settings"):
                try: tab._save_settings()
                except Exception: pass
            
            if hasattr(tab, "cleanup"):
                try: tab.cleanup()
                except Exception: pass

    def _setup_log_dock(self):
        """[Phase 2] 전역 하단 로그 도크 — 모든 탭의 메시지를 통합 수신."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 탭 디자인
        self.log_tabs = QTabWidget()
        self.log_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; background: #050a15; border-top: 1px solid #0f3460; }}
            QTabBar::tab {{
                background: #0c1428; color: #506080; padding: 6px 14px;
                font-family: '{Fonts.MONO}'; font-size: 11px; font-weight: bold;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{ background: #050a15; color: #4ecdc4; border-bottom: 2px solid #4ecdc4; }}
            QTabBar::tab:hover {{ color: #c0d0ff; }}
        """)

        _LOG_STYLE = f"background: #080e1e; border: none; color: #c0d0ff; font-family: '{Fonts.MONO}'; font-size: 12px;"
        self.txt_sys = QTextEdit(); self.txt_sys.setReadOnly(True); self.txt_sys.setStyleSheet(_LOG_STYLE)
        self.txt_dev = QTextEdit(); self.txt_dev.setReadOnly(True); self.txt_dev.setStyleSheet(_LOG_STYLE)
        self.txt_camera = QTextEdit(); self.txt_camera.setReadOnly(True); self.txt_camera.setStyleSheet(_LOG_STYLE)
        self.txt_calc = QTextEdit(); self.txt_calc.setReadOnly(True); self.txt_calc.setStyleSheet(_LOG_STYLE)

        self.log_tabs.addTab(self.txt_sys, "SYSTEM")
        self.log_tabs.addTab(self.txt_dev, "DEVICE")
        self.log_tabs.addTab(self.txt_camera, "CAMERA")
        self.log_tabs.addTab(self.txt_calc, "CALC")
        layout.addWidget(self.log_tabs)

        self.dock_log = QDockWidget("📜  OUTPUT", self)
        self.dock_log.setObjectName("dock_global_log")
        self.dock_log.setWidget(container)
        self.dock_log.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.dock_log.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        
        # 커스텀 헤더 (옵션)
        hdr = QWidget()
        hdr.setFixedHeight(24)
        hdr.setStyleSheet("background: #0c1428; border-bottom: 1px solid #1a3060;")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(10, 0, 10, 0)
        lbl = QLabel("📜  SYSTEM CONSOLE")
        lbl.setStyleSheet(f"color: {C_ACCENT}; font-size: 11px; font-weight: bold;")
        hl.addWidget(lbl); hl.addStretch()
        self.dock_log.setTitleBarWidget(hdr)

        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_log)

    def _log(self, msg: str, category: str = "sys"):
        """전역 로깅 콜백 — 카테고리에 맞춰 UI 업데이트."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        ts_html = f"<span style='color:#4a5a7a; font-family: {Fonts.MONO}; font-size:11px;'>[{ts}]</span>"
        
        cat_tag = ""
        if category != "sys":
            tag_color = "#aa7acc" if category == "calc" else "#4ecdc4" if category == "camera" else "#ff9f43"
            cat_tag = f"<span style='color:{tag_color}; font-weight:bold;'>[{category.upper()}]</span> "

        color = "#c0d0ff"
        msg_lower = msg.lower()
        if any(x in msg_lower for x in ["성공", "connected", "ok", "success"]): color = "#4ecdc4"
        elif any(x in msg_lower for x in ["실패", "failed", "error", "위반", "❌"]): color = "#e94560"
        elif any(x in msg_lower for x in ["⚠", "⚠️", "warning"]): color = "#ffe66d"

        msg_html = f"<span style='color:{color}; font-family: {Fonts.MONO};'>{msg}</span>"
        html = f"{ts_html} {cat_tag}{msg_html}"

        target = self.txt_sys
        if category == "dev": target = self.txt_dev
        elif category == "camera": target = self.txt_camera
        elif category == "calc": target = self.txt_calc
        
        target.append(html)
        target.moveCursor(target.textCursor().MoveOperation.End)

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
        for tab in (self.live_tab, self.acq_tab, self.scan_tab, self.af_tab, self.kin_tab, self.analysis_tab, self.deep_align_tab):
            if hasattr(tab, "_restore_settings"):
                try:
                    tab._restore_settings()
                except Exception as e:
                    print(f"Error restoring settings for {type(tab).__name__}: {e}")

    def closeEvent(self, event):
        """프로그램 종료 시 모든 탭의 폴링/타이머/워커를 안전하게 정지"""
        try:
            # 주요 탭들 순회하며 정리 (Live, Kinematic 등)
            all_tabs = [
                self.live_tab, self.acq_tab, self.scan_tab, 
                self.af_tab, self.kin_tab, self.analysis_tab, self.deep_align_tab
            ]
            for tab in all_tabs:
                if hasattr(tab, "stop_polling"):
                    try:
                        tab.stop_polling()
                    except Exception as e:
                        print(f"Error stopping polling for {type(tab).__name__}: {e}")
        except Exception as e:
            print(f"Close cleanup error: {e}")
            
        self.save_all_settings()
        super().closeEvent(event)
