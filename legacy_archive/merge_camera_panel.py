import os

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\camera_panel.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# I will use a script to replace only the specific methods I want to change
# and keep the rest (PROCESSING, SPATIAL, etc.)

# Actually, I'll just write a script that intelligently replaces the first half
# but keeps the second half.

class_split = content.split("class CameraControlPanel(QWidget):")
pre_class = class_split[0]
post_class = class_split[1]

# Split the post_class into methods
# We want to replace everything from __init__ to _set_connected, 
# and also add _on_apply_frames, _on_apply_temp, _on_apply_adc.

# Let's find where "PROCESSING" starts
processing_start = post_class.find("# ── 이진화 / Centroid 그룹")
if processing_start == -1:
    processing_start = post_class.find("self.grp_proc = CollapsibleSection")

second_half = post_class[processing_start:]

new_first_half = """
    camera_scan_requested   = pyqtSignal()
    camera_connect_requested= pyqtSignal(int)
    camera_disconnect_requested = pyqtSignal()
    camera_start_requested  = pyqtSignal()
    camera_stop_requested   = pyqtSignal()
    snap_requested          = pyqtSignal()
    bg_capture_requested    = pyqtSignal()
    log_message             = pyqtSignal(str)
    exposure_applied        = pyqtSignal(float)

    def __init__(self, processor: ImageProcessor, parent=None):
        super().__init__(parent)
        self._proc = processor
        self._camera = None
        self._caps = None
        self._temp_thread = None
        self._cmd_thread = None
        self._cmd_worker = None
        self._settings = QSettings("SpeAnalyze", "CameraPanel")
        self._sections = []
        self._build_ui()
        self._load_settings()
        self._set_connected(False)

    def stop_polling(self):
        if self._temp_thread and self._temp_thread.isRunning():
            self._temp_thread.stop(); self._temp_thread.wait()
        if self._cmd_thread and self._cmd_thread.isRunning():
            self._cmd_thread.quit(); self._cmd_thread.wait()

    def _build_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(6,6,6,6); layout.setSpacing(6)
        
        # 1. CAMERA DEVICE
        grp_dev = CollapsibleSection("CAMERA DEVICE", accent=C_ACCENT)
        gd = grp_dev.content_layout()
        row_t = QHBoxLayout()
        lbl_t = QLabel("Type:"); lbl_t.setStyleSheet(_LBL_STYLE)
        self.combo_cam_type = QComboBox(); self.combo_cam_type.addItems(["HIKVISION", "Picam", "SIMULATED"]); self.combo_cam_type.setStyleSheet(COMBO_STYLE)
        row_t.addWidget(lbl_t); row_t.addWidget(self.combo_cam_type, 1); gd.addLayout(row_t)
        self.camera_list = QListWidget(); self.camera_list.setFixedHeight(64)
        self.camera_list.setStyleSheet(f"QListWidget {{ background: #080e1e; border: 1px solid #0f3460; color: #8090a8; font-family: '{Fonts.MONO}'; font-size: 13px; }} QListWidget::item:selected {{ background: #0f3460; color: {C_ACCENT}; }}")
        gd.addWidget(self.camera_list)
        r1 = QHBoxLayout(); self.btn_scan = QPushButton("SCAN"); self.btn_connect = QPushButton("CONNECT"); self.btn_disconnect = QPushButton("DISCONNECT")
        for b in (self.btn_scan, self.btn_connect, self.btn_disconnect): b.setStyleSheet(_BTN_STYLE)
        r1.addWidget(self.btn_scan); r1.addWidget(self.btn_connect); r1.addWidget(self.btn_disconnect); gd.addLayout(r1)
        r2 = QHBoxLayout(); self.btn_start = QPushButton("▶ START LIVE"); self.btn_stop = QPushButton("■ STOP LIVE")
        self.btn_start.setStyleSheet(_BTN_STYLE); self.btn_stop.setStyleSheet(_BTN_STYLE.replace("#4ecdc4", C_DANGER))
        r2.addWidget(self.btn_start); r2.addWidget(self.btn_stop); gd.addLayout(r2)
        self.btn_snap = QPushButton("📷 SNAP"); self.btn_snap.setStyleSheet(_BTN_STYLE.replace("#4ecdc4", "#ffe66d"))
        gd.addWidget(self.btn_snap)
        self.bar_snap_progress = QProgressBar(); self.bar_snap_progress.setFixedHeight(4); self.bar_snap_progress.setTextVisible(False); self.bar_snap_progress.setStyleSheet("QProgressBar { background: transparent; border: none; } QProgressBar::chunk { background: #ffe66d; border-radius: 2px; }")
        gd.addWidget(self.bar_snap_progress)
        self._sections.append(grp_dev); layout.addWidget(grp_dev)

        # 2. CAMERA CONFIGURATION
        self.grp_config = CollapsibleSection("CAMERA CONFIGURATION", accent=C_ACCENT)
        gc = self.grp_config.content_layout()
        re = QHBoxLayout(); le = QLabel("Exposure (ms):"); le.setStyleSheet(_LBL_STYLE); le.setFixedWidth(100)
        self.spin_exposure = QDoubleSpinBox(); self.spin_exposure.setRange(0.01, 1000000); self.spin_exposure.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_exp = QPushButton("SET"); self.btn_apply_exp.setFixedWidth(45); self.btn_apply_exp.setStyleSheet(_BTN_STYLE)
        re.addWidget(le); re.addWidget(self.spin_exposure, 1); re.addWidget(self.btn_apply_exp); gc.addLayout(re)
        self.row_frames = QWidget(); rf = QHBoxLayout(self.row_frames); rf.setContentsMargins(0,0,0,0)
        lf = QLabel("Frames:"); lf.setStyleSheet(_LBL_STYLE); lf.setFixedWidth(100)
        self.spin_readout_count = QSpinBox(); self.spin_readout_count.setRange(1, 1000000); self.spin_readout_count.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_frames = QPushButton("SET"); self.btn_apply_frames.setFixedWidth(45); self.btn_apply_frames.setStyleSheet(_BTN_STYLE)
        rf.addWidget(lf); rf.addWidget(self.spin_readout_count, 1); rf.addWidget(self.btn_apply_frames); gc.addWidget(self.row_frames)
        self.div_config = QFrame(frameShape=QFrame.Shape.HLine, styleSheet="color: #0f3460;"); gc.addWidget(self.div_config)
        self.container_temp = QWidget(); lt = QVBoxLayout(self.container_temp); lt.setContentsMargins(0,4,0,4)
        rt = QHBoxLayout(); ltp = QLabel("Setpoint (°C):"); ltp.setStyleSheet(_LBL_STYLE); ltp.setFixedWidth(100)
        self.spin_temp = QDoubleSpinBox(); self.spin_temp.setRange(-100, 50); self.spin_temp.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_temp = QPushButton("SET"); self.btn_apply_temp.setFixedWidth(45); self.btn_apply_temp.setStyleSheet(_BTN_STYLE)
        rt.addWidget(ltp); rt.addWidget(self.spin_temp, 1); rt.addWidget(self.btn_apply_temp); lt.addLayout(rt)
        self.lbl_temp_status = QLabel("Reading: —"); self.lbl_temp_status.setStyleSheet(f"color: #a0c8ff; font-family: '{Fonts.MONO}'; font-size: 12px; padding-left: 104px;")
        lt.addWidget(self.lbl_temp_status); gc.addWidget(self.container_temp)
        self.container_adc = QWidget(); la = QVBoxLayout(self.container_adc); la.setContentsMargins(0,4,0,4); self._adc_combos = {}
        for k, l in [("adc_quality", "Quality"), ("adc_speed", "Speed"), ("adc_analog_gain", "Gain"), ("bit_depth", "Depth")]:
            r = QHBoxLayout(); lb = QLabel(f"{l}:"); lb.setStyleSheet(_LBL_STYLE); lb.setFixedWidth(100); cb = QComboBox(); cb.setStyleSheet(COMBO_STYLE)
            r.addWidget(lb); r.addWidget(cb, 1); la.addLayout(r); self._adc_combos[k] = cb
        self.btn_apply_adc = QPushButton("APPLY ADC SETTINGS"); self.btn_apply_adc.setStyleSheet(_BTN_STYLE); la.addWidget(self.btn_apply_adc); gc.addWidget(self.container_adc)
        self._sections.append(self.grp_config); layout.addWidget(self.grp_config)

"""

# Reconstruct everything
final_content = pre_class + "class CameraControlPanel(QWidget):" + new_first_half + "        " + second_half

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(final_content)
