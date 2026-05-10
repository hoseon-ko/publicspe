import os
import re

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\camera_panel.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# 1. Update _build_ui to have consistent names for containers
# We'll use self.container_config, self.row_frames, self.container_temp, self.container_adc
# ... Actually I'll just rewrite the _build_ui part carefully ...

# 2. Update attach_camera to use these names
# 3. Update slot methods

# Let's use a script to ensure the complex UI structure is correctly injected
new_build_ui = """    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── 1. CAMERA DEVICE (Connection) ───────────────────────────
        grp_dev = CollapsibleSection("CAMERA DEVICE", accent=C_ACCENT)
        gd = grp_dev.content_layout()

        # Type Selection
        row_type = QHBoxLayout()
        lbl_type = QLabel("Type:")
        lbl_type.setStyleSheet(_LBL_STYLE)
        self.combo_cam_type = QComboBox()
        self.combo_cam_type.addItems(["HIKVISION", "Picam", "SIMULATED"])
        self.combo_cam_type.setStyleSheet(COMBO_STYLE)
        row_type.addWidget(lbl_type)
        row_type.addWidget(self.combo_cam_type, 1)
        gd.addLayout(row_type)

        # Device List
        self.camera_list = QListWidget()
        self.camera_list.setFixedHeight(64)
        self.camera_list.setStyleSheet(f"QListWidget {{ background: #080e1e; border: 1px solid #0f3460; color: #8090a8; font-family: '{Fonts.MONO}'; font-size: 13px; }} QListWidget::item:selected {{ background: #0f3460; color: {C_ACCENT}; }}")
        gd.addWidget(self.camera_list)

        # Connection Buttons
        btns_conn = QHBoxLayout()
        self.btn_scan = QPushButton("SCAN")
        self.btn_connect = QPushButton("CONNECT")
        self.btn_disconnect = QPushButton("DISCONNECT")
        for b in (self.btn_scan, self.btn_connect, self.btn_disconnect): b.setStyleSheet(_BTN_STYLE)
        btns_conn.addWidget(self.btn_scan); btns_conn.addWidget(self.btn_connect); btns_conn.addWidget(self.btn_disconnect)
        gd.addLayout(btns_conn)

        # Control Buttons
        btns_ctrl = QHBoxLayout()
        self.btn_start = QPushButton("▶ START LIVE")
        self.btn_stop = QPushButton("■ STOP LIVE")
        self.btn_start.setStyleSheet(_BTN_STYLE)
        self.btn_stop.setStyleSheet(_BTN_STYLE.replace("#4ecdc4", C_DANGER))
        btns_ctrl.addWidget(self.btn_start); btns_ctrl.addWidget(self.btn_stop)
        gd.addLayout(btns_ctrl)

        # Snap
        self.btn_snap = QPushButton("📷 SNAP")
        self.btn_snap.setStyleSheet(_BTN_STYLE.replace("#4ecdc4", "#ffe66d"))
        gd.addWidget(self.btn_snap)

        self.bar_snap_progress = QProgressBar()
        self.bar_snap_progress.setFixedHeight(4); self.bar_snap_progress.setTextVisible(False)
        self.bar_snap_progress.setStyleSheet("QProgressBar { background: transparent; border: none; } QProgressBar::chunk { background: #ffe66d; border-radius: 2px; }")
        gd.addWidget(self.bar_snap_progress)

        self._sections.append(grp_dev)
        layout.addWidget(grp_dev)

        # ── 2. CAMERA CONFIGURATION (Unified Parameters) ─────────────
        self.grp_config = CollapsibleSection("CAMERA CONFIGURATION", accent=C_ACCENT)
        gc = self.grp_config.content_layout()

        # [Exposure]
        row_exp = QHBoxLayout()
        lbl_e = QLabel("Exposure (ms):")
        lbl_e.setStyleSheet(_LBL_STYLE); lbl_e.setFixedWidth(100)
        self.spin_exposure = QDoubleSpinBox()
        self.spin_exposure.setRange(0.01, 1000000); self.spin_exposure.setDecimals(2); self.spin_exposure.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_exp = QPushButton("SET")
        self.btn_apply_exp.setFixedWidth(45); self.btn_apply_exp.setStyleSheet(_BTN_STYLE)
        row_exp.addWidget(lbl_e); row_exp.addWidget(self.spin_exposure, 1); row_exp.addWidget(self.btn_apply_exp)
        gc.addLayout(row_exp)

        # [Frames / Readout Count]
        self.row_frames = QWidget()
        row_f_lay = QHBoxLayout(self.row_frames); row_f_lay.setContentsMargins(0,0,0,0)
        lbl_f = QLabel("Frames:")
        lbl_f.setStyleSheet(_LBL_STYLE); lbl_f.setFixedWidth(100)
        self.spin_readout_count = QSpinBox()
        self.spin_readout_count.setRange(1, 1000000); self.spin_readout_count.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_frames = QPushButton("SET")
        self.btn_apply_frames.setFixedWidth(45); self.btn_apply_frames.setStyleSheet(_BTN_STYLE)
        row_f_lay.addWidget(lbl_f); row_f_lay.addWidget(self.spin_readout_count, 1); row_f_lay.addWidget(self.btn_apply_frames)
        gc.addWidget(self.row_frames)

        # Divider
        self.div_config = QFrame(frameShape=QFrame.Shape.HLine, styleSheet="color: #0f3460;")
        gc.addWidget(self.div_config)

        # [Temperature]
        self.container_temp = QWidget()
        lay_t = QVBoxLayout(self.container_temp); lay_t.setContentsMargins(0, 4, 0, 4)
        row_t = QHBoxLayout()
        lbl_t = QLabel("Setpoint (°C):")
        lbl_t.setStyleSheet(_LBL_STYLE); lbl_t.setFixedWidth(100)
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(-100, 50); self.spin_temp.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_temp = QPushButton("SET")
        self.btn_apply_temp.setFixedWidth(45); self.btn_apply_temp.setStyleSheet(_BTN_STYLE)
        row_t.addWidget(lbl_t); row_t.addWidget(self.spin_temp, 1); row_t.addWidget(self.btn_apply_temp)
        lay_t.addLayout(row_t)
        self.lbl_temp_status = QLabel("Reading: —")
        self.lbl_temp_status.setStyleSheet(f"color: #a0c8ff; font-family: '{Fonts.MONO}'; font-size: 12px; padding-left: 104px;")
        lay_t.addWidget(self.lbl_temp_status)
        gc.addWidget(self.container_temp)

        # [ADC]
        self.container_adc = QWidget()
        lay_adc = QVBoxLayout(self.container_adc); lay_adc.setContentsMargins(0, 4, 0, 4)
        self._adc_combos = {}
        for key, label in [("adc_quality", "Quality"), ("adc_speed", "Speed"), ("adc_analog_gain", "Gain"), ("bit_depth", "Depth")]:
            r = QHBoxLayout()
            lbl = QLabel(f"{label}:"); lbl.setStyleSheet(_LBL_STYLE); lbl.setFixedWidth(100)
            cb = QComboBox(); cb.setStyleSheet(COMBO_STYLE)
            r.addWidget(lbl); r.addWidget(cb, 1)
            lay_adc.addLayout(r)
            self._adc_combos[key] = cb
        self.btn_apply_adc = QPushButton("APPLY ADC SETTINGS")
        self.btn_apply_adc.setStyleSheet(_BTN_STYLE)
        lay_adc.addWidget(self.btn_apply_adc)
        gc.addWidget(self.container_adc)

        self._sections.append(self.grp_config)
        layout.addWidget(self.grp_config)"""

# Replace _build_ui
content = re.sub(r'def _build_ui\(self\):.*?self\._sections\.append\(self\.grp_config\)\s+layout\.addWidget\(self\.grp_config\)', new_build_ui, content, flags=re.DOTALL)

# Replace attach_camera
new_attach = """    def attach_camera(self, camera: BaseCamera):
        self._camera = camera
        self._caps = camera.capabilities
        self._set_connected(True)

        # Visibility based on caps
        self.container_temp.setVisible(self._caps.has_temperature)
        self.container_adc.setVisible(self._caps.has_adc)
        
        from core.camera.picamp import PicamCamera
        is_picam = isinstance(camera, PicamCamera)
        self.row_frames.setVisible(is_picam)
        self.div_config.setVisible(self._caps.has_temperature or self._caps.has_adc or is_picam)

        try:
            self.spin_exposure.setValue(camera.get_exposure_ms())
        except Exception: pass

        if self._caps.has_temperature:
            try:
                mn, mx = self._caps.temperature_range_c
                if mn is not None: self.spin_temp.setMinimum(mn)
                if mx is not None: self.spin_temp.setMaximum(mx)
            except Exception: pass
            self._temp_thread = TempPollerThread(camera, 3000)
            self._temp_thread.temp_read.connect(self._on_temp_read)
            self._temp_thread.start()

        if self._caps.has_adc:
            for key, opts in [("adc_quality", self._caps.adc_quality_options), ("adc_speed", self._caps.adc_speed_options), ("adc_analog_gain", self._caps.adc_gain_options), ("bit_depth", self._caps.adc_bit_depth_options)]:
                cb = self._adc_combos[key]
                cb.clear(); cb.addItem("(default)"); cb.addItems([str(x) for x in opts])
        
        self._load_settings()"""

# Find attach_camera and replace it
content = re.sub(r'def attach_camera\(self, camera: BaseCamera\):.*?self\._load_settings\(\)', new_attach, content, flags=re.DOTALL)

# Update _apply_capabilities to not hide config
content = content.replace("self.grp_config.setVisible(False)", "")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
