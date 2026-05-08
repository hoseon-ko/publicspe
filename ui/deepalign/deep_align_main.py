import logging
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QMainWindow, 
                             QDockWidget, QToolBar, QStatusBar, QPushButton, 
                             QLabel, QProgressBar, QFrame, QStackedWidget, QGridLayout)
from PyQt6.QtCore import Qt, pyqtSignal

class DeepAlignMainTab(QWidget):
    """
    DeepAlign 통합 워크스페이스.
    기존 탭들을 대체할 수 있는 전문가용 도킹 기반 UI의 메인 컨테이너.
    """
    def __init__(self, parent=None, camera=None, acs=None, picos=None, kimmz=None):
        super().__init__(parent)
        self.camera = camera
        self.acs = acs
        self.picos = picos
        self.kimmz = kimmz
        
        self._init_ui()

    def _init_ui(self):
        # 1. 메인 레이아웃 설정
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 2. 좌측 아이콘 사이드바
        self.sidebar = self._create_sidebar()
        self.main_layout.addWidget(self.sidebar)

        # 3. 중앙 메인 워크스페이스
        self.workspace = QMainWindow()
        self.workspace.setWindowFlags(Qt.WindowType.Widget)
        self.workspace.setObjectName("deepAlignWorkspace")
        
        self.central_stack = QStackedWidget()
        self.central_stack.setObjectName("deepAlignStack")
        self.workspace.setCentralWidget(self.central_stack)
        
        # 4. 하단 마스터 바
        self.master_bar = self._create_master_bar()
        
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.workspace)
        content_layout.addWidget(self.master_bar)
        
        self.main_layout.addWidget(content_container)

        # 5. [중요] 모든 스타일을 하나의 덩어리로 관리 (충돌 방지)
        self.setStyleSheet("""
            QWidget#deepAlignWorkspace { background-color: #05080c; border: none; }
            QWidget#deepAlignStack { background-color: #000000; border: 2px solid #1e293b; margin: 10px; border-radius: 8px; }
            
            /* Sidebar Styles */
            QFrame#sidebarFrame { background-color: #0d121d; border-right: 1px solid #1e293b; }
            QPushButton.sidebarBtn { 
                background: transparent; color: #64748b; border: none; 
                padding: 15px 5px; font-weight: 800; font-size: 11px;
            }
            QPushButton.sidebarBtn:checked { color: #4ecdc4; background: rgba(78, 205, 196, 0.1); border-left: 3px solid #4ecdc4; }
            
            /* Master Bar Styles */
            QFrame#masterBarFrame { background-color: #0d121d; border-top: 2px solid #334155; }
            QFrame.modContainer { background-color: #131a29; border: 1px solid #334155; border-radius: 6px; }
            QFrame#progContainer { background-color: #020617; border: 2px solid #1e293b; border-radius: 8px; }
            
            /* Text Controls */
            QLabel { color: #e2e8f0; font-family: 'Segoe UI', 'Malgun Gothic', 'Arial'; }
            QLabel.statusHeader { color: #f59e0b; font-weight: 900; font-size: 15px; }
            QLabel.timeLabel { color: #94a3b8; font-size: 11px; font-weight: bold; }
            QLabel.metaLabel { color: #94a3b8; font-size: 11px; font-weight: 900; }
            QLabel.healthVal { font-size: 15px; font-weight: 900; }
            
            /* Button Controls */
            QPushButton.actionBtn { color: #ffffff; font-size: 13px; font-weight: 800; border-radius: 4px; }
            QPushButton#snapBtn { background-color: #2563eb; }
            QPushButton#liveBtn { background-color: #14b8a6; }
            QPushButton#acqBtn { background-color: #e11d48; }
            QPushButton#stopBtn { background-color: #7f1d1d; color: #fca5a5; border: 2px solid #b91c1c; }
            
            /* Progress Bar */
            QProgressBar { background: #0f172a; border-radius: 4px; border: 1px solid #334155; }
            QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f43f5e, stop:1 #fb7185); border-radius: 3px; }
        """)

    def _create_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebarFrame")
        sidebar.setFixedWidth(65)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 20, 0, 20)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.btn_cam = QPushButton("CAM\nENG"); self.btn_cam.setProperty("class", "sidebarBtn"); self.btn_cam.setCheckable(True)
        self.btn_mir = QPushButton("MIR\nADJ"); self.btn_mir.setProperty("class", "sidebarBtn"); self.btn_mir.setCheckable(True)
        self.btn_foc = QPushButton("FOC\nCTL"); self.btn_foc.setProperty("class", "sidebarBtn"); self.btn_foc.setCheckable(True)
        self.btn_aln = QPushButton("ALN\nSTG"); self.btn_aln.setProperty("class", "sidebarBtn"); self.btn_aln.setCheckable(True)
        self.btn_ana = QPushButton("ANA\nLYS"); self.btn_ana.setProperty("class", "sidebarBtn"); self.btn_ana.setCheckable(True)

        self.btn_cam.setChecked(True)
        for btn in [self.btn_cam, self.btn_mir, self.btn_foc, self.btn_aln, self.btn_ana]:
            btn.setFixedWidth(65)
            layout.addWidget(btn)
        
        layout.addStretch()
        return sidebar

    def _create_master_bar(self):
        bar = QFrame()
        bar.setObjectName("masterBarFrame")
        bar.setFixedHeight(85) 
        main_layout = QHBoxLayout(bar)
        main_layout.setContentsMargins(15, 8, 15, 8)
        main_layout.setSpacing(15)

        # --- [1] LEFT: CONTROL BUTTONS ---
        ctrl_mod = QWidget()
        ctrl_lay = QHBoxLayout(ctrl_mod); ctrl_lay.setContentsMargins(0,0,0,0); ctrl_lay.setSpacing(6)
        
        def create_fancy_btn(obj_name, main_txt, sub_txt, color):
            btn = QPushButton(); btn.setObjectName(obj_name); btn.setFixedSize(90, 48)
            btn.setCheckable(True)
            l = QVBoxLayout(btn); l.setSpacing(0); l.setContentsMargins(0,4,0,4)
            mt = QLabel(main_txt); mt.setStyleSheet("font-size: 13px; font-weight: 900; color: white; background:transparent; border:none;")
            st = QLabel(sub_txt); st.setStyleSheet("font-size: 7px; font-weight: 700; color: rgba(255,255,255,0.5); background:transparent; border:none;")
            mt.setAlignment(Qt.AlignmentFlag.AlignCenter); st.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.addWidget(mt); l.addWidget(st)
            return btn

        self.snap_btn = create_fancy_btn("snapBtn", "SNAP", "ONE SHOT", "#2563eb")
        self.live_btn = create_fancy_btn("liveBtn", "LIVE", "ON AIR", "#14b8a6")
        self.acq_btn = create_fancy_btn("acqBtn", "ACQUIRE", "RECORDING", "#e11d48")
        self.stop_btn = create_fancy_btn("stopBtn", "STOP", "FORCE EXIT", "#7f1d1d")
        
        for btn in [self.snap_btn, self.live_btn, self.acq_btn, self.stop_btn]:
            ctrl_lay.addWidget(btn)
        main_layout.addWidget(ctrl_mod)

        # --- [2] CENTER: PROGRESS & TIME ---
        center_mod = QWidget()
        center_lay = QVBoxLayout(center_mod); center_lay.setContentsMargins(5,0,5,0); center_lay.setSpacing(4)
        
        # Upper: Frame & Time
        time_row = QHBoxLayout(); time_row.setContentsMargins(0,0,0,0)
        self.lbl_frames = QLabel("FRAME: 0 / 0 (0.0%)")
        self.lbl_frames.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 800; border:none;")
        time_row.addWidget(self.lbl_frames)
        
        time_row.addStretch()

        def add_t(lay, label):
            l = QLabel(label); l.setStyleSheet("color: #475569; font-size: 9px; font-weight: 800; border:none;")
            v = QLabel("00:00:00"); v.setStyleSheet("color: #f8fafc; font-size: 11px; font-weight: 900; border:none;")
            lay.addWidget(l); lay.addWidget(v)
            return v

        self.val_elapsed = add_t(time_row, "ELAPSED:")
        sep1 = QLabel("|"); sep1.setStyleSheet("color: #334155; border:none;"); time_row.addWidget(sep1)
        self.val_remain = add_t(time_row, "REMAIN:")
        sep2 = QLabel("|"); sep2.setStyleSheet("color: #334155; border:none;"); time_row.addWidget(sep2)
        self.val_eta = add_t(time_row, "ETA:")
        center_lay.addLayout(time_row)

        # Lower: Progress Bar
        self.progress_bar = QProgressBar(); self.progress_bar.setFixedHeight(18)
        self.progress_bar.setTextVisible(True); self.progress_bar.setFormat(" %p% COMPLETE")
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_lay.addWidget(self.progress_bar)
        main_layout.addWidget(center_mod, stretch=2)

        # --- [3] RIGHT: HEALTH DASHBOARD (Grid Style) ---
        health_mod = QWidget()
        health_lay = QHBoxLayout(health_mod); health_lay.setContentsMargins(5,0,0,0); health_lay.setSpacing(12)
        
        def add_h_stack(label, color):
            container = QWidget()
            l = QVBoxLayout(container); l.setSpacing(2); l.setContentsMargins(0,0,0,0)
            lbl = QLabel(label); lbl.setStyleSheet("color: #64748b; font-size: 8px; font-weight: 800; border:none;")
            val = QLabel("---"); val.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 900; border:none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.addWidget(lbl); l.addWidget(val)
            health_lay.addWidget(container)
            return val

        self.lbl_dropped = add_h_stack("DROPPED", "#f43f5e")
        self.lbl_write = add_h_stack("WRITE RATE", "#22d3ee")
        self.lbl_disk = add_h_stack("STORAGE", "#a78bfa")
        self.lbl_buffer = add_h_stack("BUFFER", "#fbbf24")
        
        main_layout.addWidget(health_mod)

        # Stylesheet
        self.setStyleSheet(self.styleSheet() + """
            QProgressBar { 
                background: #0f172a; border-radius: 9px; border: 1px solid #334155; 
                color: white; font-size: 9px; font-weight: 900;
            }
            QProgressBar::chunk { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f43f5e, stop:1 #fb7185); 
                border-radius: 8px; 
            }
            QPushButton#snapBtn { background-color: #2563eb; border: none; border-radius: 6px; }
            QPushButton#liveBtn { background-color: #14b8a6; border: none; border-radius: 6px; }
            QPushButton#acqBtn { background-color: #e11d48; border: none; border-radius: 6px; }
            QPushButton#stopBtn { background-color: #450a0a; border: 1px solid #ef4444; border-radius: 6px; }
            QPushButton:hover { background-color: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.2); }
            QPushButton:pressed { background-color: rgba(0,0,0,0.2); }
        """)

        return bar

    # ── 하드웨어 공유 메서드 (MainWindow 연동) ──────────────────

    def set_shared_camera(self, cam):
        self.camera = cam
        cam_name = getattr(cam, "model_name", "Connected")
        self.lbl_status.setText(f"STATUS: READY ({cam_name})")
        logging.info(f"[DeepAlign] Camera connected: {cam_name}")

    def clear_shared_camera(self):
        self.camera = None
        self.lbl_status.setText("STATUS: DISCONNECTED")
        logging.info("[DeepAlign] Camera disconnected")

    def set_acs_ctrl(self, acs):
        self.acs = acs
        logging.info("[DeepAlign] ACS Controller linked")

    def clear_acs_ctrl(self):
        self.acs = None
        logging.info("[DeepAlign] ACS Controller unlinked")

    def set_kimm_ctrl(self, kimmz):
        self.kimmz = kimmz
        logging.info("[DeepAlign] KIMM-Z Controller linked")

    def clear_kimm_ctrl(self):
        self.kimmz = None
        logging.info("[DeepAlign] KIMM-Z Controller unlinked")

    def set_picos_ctrl(self, picos):
        self.picos = picos
        logging.info("[DeepAlign] Picomotor Controller linked")
