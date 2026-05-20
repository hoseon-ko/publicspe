"""
ui/main_window.py
DeepAlign 전용 메인 윈도우.

레이아웃:
  ┌─ 헤더 바 ─────────────────────────────────────────────────────┐
  │  SpeAnalyze  │ 🌌 DeepAlign │              AUTO CONNECT │
  ├──────────────────────────────────────────────────────────────┤
  │                                                              │
  │   DeepAlignMainTab                                           │
  │                                                              │
  └──────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QStackedWidget, QStatusBar,
    QLabel, QFrame, QVBoxLayout, QHBoxLayout, QPushButton,
    QDockWidget, QTextEdit, QTabWidget, QApplication, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QShortcut, QKeySequence

from core.config import get_config
from core.hal.adapters import HikvisionCameraAdapter, PicamCameraAdapter, SimulatedCameraAdapter
from core.session.device_session_hub import DeviceSessionHub
from core.logger import app_logger, register_ui_callback
from theme.styles import Fonts, Sizes, C_ACCENT, C_TEXT_DIM, C_BG_MED, C_BORDER
from ui.deepalign.deepalign_main_tab import DeepAlignMainTab
from ui.bridge.hub_bindings import bind_status_to_main_window


# ── 헤더 바 색상 ──────────────────────────────────────────────────
_HDR_BG      = "#161b27"
_HDR_BORDER  = "#1e2a3e"
_TAB_NORMAL  = "#4a5a70"
_TAB_ACTIVE  = "#e0e8f0"
_TAB_LINE    = C_ACCENT


class StartupOverlay(QWidget):
    """장비 초기 자동 연동 중 UI 상호작용을 차단하고 진행 상태를 보여주는 독립형 스플래시 윈도우"""
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setFixedSize(540, 500)
        if QApplication.instance():
            screen = QApplication.primaryScreen().geometry()
            x = (screen.width() - self.width()) // 2
            y = (screen.height() - self.height()) // 2
            self.move(x, y)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #0d1326;
                border: 2px solid #4ecdc4;
                border-radius: 12px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(40, 40, 40, 40)
        card_layout.setSpacing(18)

        lbl_title = QLabel("SYSTEM INITIALIZATION")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setStyleSheet("color: #4ecdc4; font-family: 'Consolas', monospace; font-size: 20px; font-weight: bold; letter-spacing: 3px; border: none;")
        card_layout.addWidget(lbl_title)

        lbl_sub = QLabel("Establishing Hardware Cockpit Connections")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_sub.setStyleSheet("color: #64748b; font-family: 'Segoe UI'; font-size: 13px; border: none;")
        card_layout.addWidget(lbl_sub)

        card_layout.addSpacing(15)

        self.status_labels = {}
        devices = [
            ("pico", "Picomotor Controller"),
            ("kimm", "KIMM Fine Z-Stage"),
            ("acs", "ACS Multi-Axis Stage"),
            ("camera", "High-Speed Camera System")
        ]

        for dev_key, dev_name in devices:
            row = QFrame()
            row.setStyleSheet("border: none; background: transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 4, 10, 4)

            lbl_name = QLabel(dev_name)
            lbl_name.setStyleSheet("color: #c0d0ff; font-family: 'Consolas', monospace; font-size: 14px; border: none;")

            lbl_status = QLabel("● STANDBY")
            lbl_status.setStyleSheet("color: #475569; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold; border: none;")
            lbl_status.setFixedWidth(140)
            lbl_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            row_layout.addWidget(lbl_name)
            row_layout.addWidget(lbl_status)
            card_layout.addWidget(row)

            self.status_labels[dev_key] = lbl_status

        card_layout.addSpacing(20)

        self.btn_skip = QPushButton("SKIP & START COCKPIT")
        self.btn_skip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_skip.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #e2e8f0;
                border: 1px solid #475569;
                border-radius: 6px;
                font-family: 'Consolas', monospace;
                font-size: 13px;
                padding: 10px;
            }
            QPushButton:hover {
                background: #1e293b;
                border-color: #94a3b8;
                color: #ffffff;
            }
        """)
        self.btn_skip.clicked.connect(self.hide_overlay)
        card_layout.addWidget(self.btn_skip)

        layout.addWidget(card)

    def set_status(self, dev_key: str, status: str):
        lbl = self.status_labels.get(dev_key)
        if not lbl:
            return

        if status == "connecting":
            lbl.setText("● CONNECTING...")
            lbl.setStyleSheet("color: #eab308; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold; border: none;")
        elif status == "success":
            lbl.setText("● CONNECTED")
            lbl.setStyleSheet("color: #10b981; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold; border: none;")
        elif status == "skipped":
            lbl.setText("● SKIPPED")
            lbl.setStyleSheet("color: #f43f5e; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold; border: none;")

    def hide_overlay(self):
        self.hide()
        self.finished.emit()


class MainWindow(QMainWindow):
    auto_connect_status = pyqtSignal(str, str)

    def __init__(self, spe_class=None):
        super().__init__()
        self._spe_class = spe_class
        self.session_hub = DeviceSessionHub(self)
        self.session_hub.register_camera_hal("hikvision", HikvisionCameraAdapter)
        self.session_hub.register_camera_hal("picam", PicamCameraAdapter)
        self.session_hub.register_camera_hal("simulated", SimulatedCameraAdapter)
        self.session_hub.select_camera_vendor("simulated")
        self.setWindowTitle("SpeAnalyze — DeepAlign")
        self.setMinimumSize(1300, 850)
        self.resize(1700, 1000)
        self._setup_log_dock()
        register_ui_callback(self._log)
        self._build_ui()

        self.startup_overlay = StartupOverlay(None)
        self.startup_overlay.finished.connect(self.show_main_window)
        self.auto_connect_status.connect(self.startup_overlay.set_status)

        self._restore_settings()
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        self.sc_dump = QShortcut(QKeySequence("Ctrl+Alt+S"), self)
        self.sc_dump.activated.connect(self.dump_ui_screenshots)

    # ── UI ───────────────────────────────────────────────────────────

    def _build_ui(self):
        _FC = Fonts.MONO

        root = QWidget()
        root.setObjectName("root")
        root.setStyleSheet("QWidget#root { background: #080e1e; }")
        root_v = QVBoxLayout(root)
        root_v.setContentsMargins(0, 0, 0, 0)
        root_v.setSpacing(0)
        self.setCentralWidget(root)

        # ── 헤더 바 ───────────────────────────────────────────────
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

        lbl_app = QLabel("SpeAnalyze")
        lbl_app.setStyleSheet(
            f"color: #4ecdc4; font-family: '{_FC}', 'Segoe UI'; font-size: 15px;"
            " font-weight: bold; letter-spacing: 2px;"
            " padding: 0 20px 0 10px;"
            f" border-right: 1px solid {_HDR_BORDER};"
        )
        hdr_h.addWidget(lbl_app)
        hdr_h.addSpacing(8)

        # ── DeepAlign 단일 탭 라벨 (스타일 유지용) ─────────────────
        _tab_qss = f"""
            QPushButton {{
                background: transparent;
                color: #ffffff;
                border: none;
                border-bottom: 3px solid {_TAB_LINE};
                font-family: 'Segoe UI', '{_FC}';
                font-size: 13px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 0 20px;
                height: 46px;
            }}
        """
        btn_deepalign = QPushButton("🌌 DeepAlign")
        btn_deepalign.setCheckable(True)
        btn_deepalign.setChecked(True)
        btn_deepalign.setFlat(True)
        btn_deepalign.setFixedHeight(46)
        btn_deepalign.setStyleSheet(_tab_qss)
        hdr_h.addWidget(btn_deepalign)

        # ── 콘텐츠 스택 (단일 탭) ─────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: #0a0f1e;")

        self.deep_align_tab = DeepAlignMainTab()
        self.deep_align_tab.bind_session_hub(self.session_hub)
        self.stack.addWidget(self.deep_align_tab)

        bind_status_to_main_window(self.session_hub, self)

        # 헤더 오른쪽: AUTO CONNECT 토글
        hdr_h.addStretch(1)

        self.check_auto_conn = QCheckBox("AUTO CONNECT")
        self.check_auto_conn.setStyleSheet(f"""
            QCheckBox {{
                color: {_TAB_NORMAL};
                font-family: '{_FC}', 'Segoe UI';
                font-size: 11px;
                font-weight: bold;
                spacing: 6px;
                margin-right: 12px;
            }}
            QCheckBox:hover {{
                color: #ffffff;
            }}
            QCheckBox::indicator {{
                width: 12px;
                height: 12px;
                border: 1px solid {_HDR_BORDER};
                background: #080e1e;
                border-radius: 2px;
            }}
            QCheckBox::indicator:checked {{
                background: {C_ACCENT};
                border-color: {C_ACCENT};
            }}
        """)
        self.check_auto_conn.setChecked(bool(get_config().get("window.main.auto_connect", True)))
        self.check_auto_conn.stateChanged.connect(self._on_auto_connect_toggled)
        hdr_h.addWidget(self.check_auto_conn)

        root_v.addWidget(header)
        root_v.addWidget(self.stack, 1)

        # ── 상태바 ─────────────────────────────────────────────────
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

    def dump_ui_screenshots(self):
        """현재 화면 스크린샷을 'UI_Debug' 폴더에 저장."""
        import os
        from datetime import datetime

        save_dir = "UI_Debug"
        os.makedirs(save_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            filename = f"deepalign_{ts}.png"
            path = os.path.join(save_dir, filename)
            pixmap = self.grab()
            pixmap.save(path, "PNG")
            app_logger.info(f"[UI Dump] Saved: {path}")
            self._on_status(f"📸 스크린샷 저장 완료 ({path})")
        except Exception as e:
            self._on_status(f"❌ 스크린샷 저장 실패: {e}")

    # ── 슬롯 ─────────────────────────────────────────────────────────

    def _on_status(self, msg: str):
        self._status_label.setText(msg)

    # ── 종료 처리 ────────────────────────────────────────────────────

    def save_all_settings(self):
        """aboutToQuit / SIGINT 등 모든 종료 경로에서 설정 저장 및 UI 상태 저장."""
        cfg = get_config()
        cfg.set("window.main.geometry", self.saveGeometry())
        cfg.set("window.main.windowState", self.saveState())
        cfg.save()

        if hasattr(self.deep_align_tab, "_save_settings"):
            try: self.deep_align_tab._save_settings()
            except Exception: pass
        if hasattr(self.deep_align_tab, "cleanup"):
            try: self.deep_align_tab.cleanup()
            except Exception: pass

    def _setup_log_dock(self):
        """전역 하단 로그 도크 — 모든 카테고리 메시지 통합 수신."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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

        hdr = QWidget()
        hdr.setFixedHeight(24)
        hdr.setStyleSheet("background: #0c1428; border-bottom: 1px solid #1a3060;")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(10, 0, 10, 0)
        lbl = QLabel("📜  SYSTEM CONSOLE")
        lbl.setStyleSheet(f"color: {C_ACCENT}; font-size: 11px; font-weight: bold;")
        hl.addWidget(lbl); hl.addStretch()
        self.dock_log.setTitleBarWidget(hdr)

        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_log)

    def _log(self, msg: str, category: str = "sys", levelno: int = 20):
        """전역 로깅 콜백 — 카테고리에 맞춰 UI 업데이트."""
        from datetime import datetime
        import logging
        ts = datetime.now().strftime("%H:%M:%S")
        ts_html = f"<span style='color:#4a5a7a; font-family: {Fonts.MONO}; font-size:11px;'>[{ts}]</span>"

        cat_tag = ""
        if category != "sys":
            tag_color = "#aa7acc" if category == "calc" else "#4ecdc4" if category == "camera" else "#ff9f43"
            cat_tag = f"<span style='color:{tag_color}; font-weight:bold;'>[{category.upper()}]</span> "

        color = "#c0d0ff"
        if levelno >= logging.ERROR:
            color = "#e94560"
        elif levelno >= logging.WARNING:
            color = "#ffe66d"
        elif levelno <= logging.DEBUG:
            color = "#6080a0"
        else:
            msg_lower = msg.lower()
            if any(x in msg_lower for x in ["성공", "connected", "ok", "success"]):
                color = "#4ecdc4"

        msg_html = f"<span style='color:{color}; font-family: {Fonts.MONO};'>{msg}</span>"
        html = f"{ts_html} {cat_tag}{msg_html}"

        target = self.txt_sys
        if category == "dev": target = self.txt_dev
        elif category == "camera": target = self.txt_camera
        elif category == "calc": target = self.txt_calc

        target.append(html)
        target.moveCursor(target.textCursor().MoveOperation.End)

    def _restore_settings(self):
        """Load saved MainWindow UI state and forward to DeepAlign tab."""
        cfg = get_config()
        try:
            geom = cfg.get("window.main.geometry")
            if geom:
                self.restoreGeometry(geom)
            state = cfg.get("window.main.windowState")
            if state:
                self.restoreState(state)
        except Exception as e:
            print(f"MainWindow settings restore error: {e}")

        if hasattr(self.deep_align_tab, "_restore_settings"):
            try:
                self.deep_align_tab._restore_settings()
            except Exception as e:
                print(f"Error restoring settings for DeepAlignMainTab: {e}")

    def closeEvent(self, event):
        """프로그램 종료 시 폴링/타이머/워커를 안전하게 정지"""
        try:
            if hasattr(self.deep_align_tab, "stop_polling"):
                try:
                    self.deep_align_tab.stop_polling()
                except Exception as e:
                    print(f"Error stopping polling for DeepAlignMainTab: {e}")
        except Exception as e:
            print(f"Close cleanup error: {e}")

        self.save_all_settings()
        super().closeEvent(event)

    def show_main_window(self):
        """스플래시 화면을 닫고 메인 윈도우를 표시합니다."""
        self.show()
        if hasattr(self, "startup_overlay"):
            self.startup_overlay.hide()
        QApplication.setQuitOnLastWindowClosed(True)

    def start_application(self):
        """어플리케이션 시작 시 자동 연결 설정에 따라 스플래시 또는 메인 화면을 노출합니다."""
        auto_connect_enabled = bool(get_config().get("window.main.auto_connect", True))
        if auto_connect_enabled:
            QApplication.setQuitOnLastWindowClosed(False)
            if hasattr(self, "startup_overlay"):
                self.startup_overlay.show()
                self.startup_overlay.raise_()
            self._auto_connect_startup()
        else:
            self.show()

    def _auto_connect_startup(self):
        """백그라운드에서 모든 하드웨어 연결 자동 시도 (예외 안전)"""
        auto_connect_enabled = bool(get_config().get("window.main.auto_connect", True))

        if not auto_connect_enabled:
            app_logger.info("[Auto-Connect] 자동 장비 연결 기능이 비활성화 상태입니다. (자동 연결 스킵)")
            if hasattr(self, "startup_overlay"):
                self.startup_overlay.hide()
            self.show()
            return

        import threading

        def worker():
            app_logger.info("[Auto-Connect] 백그라운드 자동 장비 연결 시퀀스 가동...")

            # 1. Picomotor 자동 연결
            try:
                self.auto_connect_status.emit("pico", "connecting")
                app_logger.info("[Auto-Connect] Picomotor 연결 시도...")
                self.session_hub.connect_pico()
                self.session_hub.start_pico_polling()
                app_logger.info("[Auto-Connect] Picomotor 연결 성공.")
                self.auto_connect_status.emit("pico", "success")
            except Exception as e:
                app_logger.warning(f"[Auto-Connect] Picomotor 연결 스킵 (점유 또는 장치 없음): {e}")
                self.auto_connect_status.emit("pico", "skipped")

            # 2. KIMM Z-Stage 자동 연결
            try:
                self.auto_connect_status.emit("kimm", "connecting")
                cfg = get_config()
                ip = cfg.get("devices.kimm.ip", "192.168.1.100")
                port_val = cfg.get("devices.kimm.port", "5000")
                port = int(port_val) if port_val else 5000
                app_logger.info(f"[Auto-Connect] KIMM Z-Stage 연결 시도 ({ip}:{port})...")
                self.session_hub.kimm_connect(ip, port)
                app_logger.info("[Auto-Connect] KIMM Z-Stage 연결 성공.")
                self.auto_connect_status.emit("kimm", "success")
            except Exception as e:
                app_logger.warning(f"[Auto-Connect] KIMM Z-Stage 연결 스킵 (타임아웃 또는 점유): {e}")
                self.auto_connect_status.emit("kimm", "skipped")

            # 3. ACS Stage 자동 연결 → GUI 스레드에서 실행
            QTimer.singleShot(100, self._auto_connect_acs_then_camera_on_gui)

        t = threading.Thread(target=worker, daemon=True, name="StartupAutoConnect")
        t.start()

    def _auto_connect_acs_then_camera_on_gui(self):
        """GUI 스레드에서 ACS 자동 연결 → 완료 후 카메라 자동 연결."""
        try:
            self.auto_connect_status.emit("acs", "connecting")
            cfg = get_config()
            ip = cfg.get("devices.acs.ip", "10.0.0.100")
            port_val = cfg.get("devices.acs.port", "700")
            port = int(port_val) if port_val else 700
            sim = bool(cfg.get("devices.acs.sim", False))
            app_logger.info(f"[Auto-Connect] ACS Stage 연결 시도 ({ip}:{port}, sim={sim})...")
            self.session_hub.acs_connect(ip, port, sim)
            app_logger.info("[Auto-Connect] ACS Stage 연결 성공.")
            self.auto_connect_status.emit("acs", "success")
        except Exception as e:
            app_logger.warning(f"[Auto-Connect] ACS Stage 연결 스킵 (타임아웃 또는 점유): {e}")
            self.auto_connect_status.emit("acs", "skipped")
        finally:
            QTimer.singleShot(100, self._auto_connect_camera_on_gui)

    def _auto_connect_camera_on_gui(self):
        """메인 GUI 스레드에서 안전하게 카메라 자동 연결 수행 (DeepAlign 탭 기준)"""
        try:
            self.auto_connect_status.emit("camera", "connecting")
            vendor = str(get_config().get("camera.last_used.vendor", "Simulation")).strip()

            app_logger.info(f"[Auto-Connect] 저장된 카메라 벤더 로드: {vendor}")

            vendor_key = vendor.lower()
            if vendor_key in ("simulation", "simulated"):
                vendor_key = "simulated"

            self.session_hub.select_camera_vendor(vendor_key)
            devices = self.session_hub.scan_cameras()

            if devices:
                dev = devices[0]
                device_id = getattr(dev, "device_id", "")
                app_logger.info(f"[Auto-Connect] 카메라 연결 시도: vendor={vendor}, device={device_id}...")
                self.session_hub.connect_camera(str(device_id))
                app_logger.info(f"[Auto-Connect] 카메라 연결 성공: {device_id}")

                self.deep_align_tab._scanned_devices = list(devices)
                self.deep_align_tab._populate_camera_list_from_devices(devices)

                idx = self.deep_align_tab.cb_vendor.findText(vendor)
                if idx >= 0:
                    self.deep_align_tab.cb_vendor.blockSignals(True)
                    self.deep_align_tab.cb_vendor.setCurrentIndex(idx)
                    self.deep_align_tab.cb_vendor.blockSignals(False)

                try:
                    caps = self.session_hub.camera_get_capabilities()
                except Exception:
                    caps = None
                self.deep_align_tab._apply_camera_capabilities(caps)

                try:
                    self.deep_align_tab._push_saved_camera_settings(caps, vendor)
                except Exception:
                    app_logger.exception("[Auto-Connect] push saved camera settings failed")

                from core.session.ownership import OWNER_DEEPALIGN
                try:
                    ms = float(self.session_hub.camera_get_exposure_ms(OWNER_DEEPALIGN))
                    self.deep_align_tab.spin_exposure.blockSignals(True)
                    self.deep_align_tab.spin_exposure.setValue(ms)
                    self.deep_align_tab.spin_exposure.blockSignals(False)
                except Exception:
                    pass

                if caps and caps.has_temperature:
                    try:
                        reading, setpoint, status = self.session_hub.camera_get_temperature(OWNER_DEEPALIGN)
                        self.deep_align_tab.lbl_temp_read.setText(f"Reading: {reading}")
                        self.deep_align_tab.lbl_temp_set.setText(f"Setpoint: {setpoint}")
                        self.deep_align_tab.lbl_temp_state.setText(f"Status: {status}")
                        if setpoint is not None:
                            self.deep_align_tab.spin_temp.blockSignals(True)
                            self.deep_align_tab.spin_temp.setValue(float(setpoint))
                            self.deep_align_tab.spin_temp.blockSignals(False)
                    except Exception:
                        pass

                if caps and caps.has_adc:
                    try:
                        adc_settings = self.session_hub.camera_get_adc_settings(OWNER_DEEPALIGN)
                        mapping = {
                            "adc_quality": self.deep_align_tab.cb_adc_quality,
                            "adc_speed": self.deep_align_tab.cb_adc_speed,
                            "adc_analog_gain": self.deep_align_tab.cb_adc_gain,
                            "bit_depth": self.deep_align_tab.cb_adc_bit,
                        }
                        for key, cb in mapping.items():
                            val = adc_settings.get(key)
                            if val is not None:
                                cb_idx = cb.findText(str(val))
                                if cb_idx >= 0:
                                    cb.blockSignals(True)
                                    cb.setCurrentIndex(cb_idx)
                                    cb.blockSignals(False)
                    except Exception:
                        pass

                self.deep_align_tab._set_camera_action_state(True)
                if caps and getattr(caps, "has_temperature", False):
                    self.deep_align_tab._start_temp_polling()
                self.auto_connect_status.emit("camera", "success")
            else:
                app_logger.info(f"[Auto-Connect] 감지된 {vendor} 카메라 장비가 없어 연결을 건너뜁니다.")
                self.auto_connect_status.emit("camera", "skipped")
        except Exception as e:
            app_logger.warning(f"[Auto-Connect] 카메라 자동 연결 실패: {e}")
            self.auto_connect_status.emit("camera", "skipped")
        finally:
            QTimer.singleShot(1200, self.startup_overlay.hide_overlay)

    def _on_auto_connect_toggled(self):
        """사용자가 헤더 바에서 AUTO CONNECT 체크박스를 토글할 때 설정 파일에 상태 저장"""
        cfg = get_config()
        checked = self.check_auto_conn.isChecked()
        cfg.set("window.main.auto_connect", checked)
        cfg.save()
        app_logger.info(f"[Auto-Connect] 자동 연결 기능 설정 변경 -> {'활성화' if checked else '비활성화'}")
