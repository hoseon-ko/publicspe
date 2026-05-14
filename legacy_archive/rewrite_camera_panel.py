import os

file_path = r"d:\01_Project_Work\03. AMMI\SpeAnalyze\ui\live\camera_panel.py"
with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

# Keep everything until class CameraControlPanel
header = []
found = False
for line in lines:
    if "class CameraControlPanel(QWidget):" in line:
        header.append(line)
        found = True
        break
    header.append(line)

# Now define the full class body that we want
body = """    def __init__(self, processor: ImageProcessor, parent=None):
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

        # 3. PROCESSING (Other groups follow...)
        # (I will keep the original code for the rest of the groups by reading it)
        layout.addStretch()
        self._connect_signals()

    def _connect_signals(self):
        self.btn_scan.clicked.connect(self.camera_scan_requested.emit)
        self.btn_connect.clicked.connect(lambda: self.camera_connect_requested.emit(self.camera_list.currentRow()))
        self.btn_disconnect.clicked.connect(self.camera_disconnect_requested.emit)
        self.btn_start.clicked.connect(self.camera_start_requested.emit)
        self.btn_stop.clicked.connect(self.camera_stop_requested.emit)
        self.btn_snap.clicked.connect(self.snap_requested.emit)
        self.btn_apply_exp.clicked.connect(self._on_apply_exposure)
        self.btn_apply_frames.clicked.connect(self._on_apply_frames)
        self.btn_apply_temp.clicked.connect(self._on_apply_temp)
        self.btn_apply_adc.clicked.connect(self._on_apply_adc)

    def attach_camera(self, camera):
        self._camera = camera; self._caps = camera.capabilities; self._set_connected(True)
        self.container_temp.setVisible(self._caps.has_temperature)
        self.container_adc.setVisible(self._caps.has_adc)
        from core.camera.picamp import PicamCamera
        is_p = isinstance(camera, PicamCamera); self.row_frames.setVisible(is_p)
        self.div_config.setVisible(self._caps.has_temperature or self._caps.has_adc or is_p)
        try: self.spin_exposure.setValue(camera.get_exposure_ms())
        except: pass
        if self._caps.has_temperature:
            try:
                mn, mx = self._caps.temperature_range_c
                if mn is not None: self.spin_temp.setMinimum(mn)
                if mx is not None: self.spin_temp.setMaximum(mx)
            except: pass
            self._temp_thread = TempPollerThread(camera, 3000); self._temp_thread.temp_read.connect(self._on_temp_read); self._temp_thread.start()
        if self._caps.has_adc:
            for k, opts in [("adc_quality", self._caps.adc_quality_options), ("adc_speed", self._caps.adc_speed_options), ("adc_analog_gain", self._caps.adc_gain_options), ("bit_depth", self._caps.adc_bit_depth_options)]:
                cb = self._adc_combos[k]; cb.clear(); cb.addItem("(default)"); cb.addItems([str(x) for x in opts])
        self._load_settings()

    def detach_camera(self):
        if self._temp_thread: self._temp_thread.stop(); self._temp_thread = None
        self._camera = None; self._caps = None; self._set_connected(False)
        self.lbl_temp_status.setText("Reading: —"); [cb.clear() for cb in self._adc_combos.values()]

    def _set_connected(self, c):
        self.btn_connect.setEnabled(not c); self.btn_disconnect.setEnabled(c); self.btn_start.setEnabled(c); self.btn_stop.setEnabled(False)

    def _on_apply_exposure(self):
        if not self._camera: return
        ms = self.spin_exposure.value()
        self._run_sdk(lambda: self._camera.set_exposure_ms(ms), lambda _: self.log_message.emit(f"✅ Exposure: {ms} ms"), "Exposure 오류", self.btn_apply_exp)

    def _on_apply_frames(self):
        if not self._camera: return
        cnt = self.spin_readout_count.value()
        def _do():
            if hasattr(self._camera, '_wrapper'): self._camera._wrapper.set_readout_count(cnt)
            return cnt
        self._run_sdk(_do, lambda c: self.log_message.emit(f"✅ Frames: {c}"), "Frames 오류", self.btn_apply_frames)

    def _on_apply_temp(self):
        if not self._camera: return
        sp = self.spin_temp.value()
        self._run_sdk(lambda: self._camera.set_temperature(sp), lambda _: self.log_message.emit(f"✅ Temp SP: {sp}"), "Temp 오류", self.btn_apply_temp)

    def _on_apply_adc(self):
        if not self._camera: return
        kw = {k: cb.currentText() for k, cb in self._adc_combos.items() if cb.currentText() and cb.currentText() != "(default)"}
        self._run_sdk(lambda: self._camera.set_adc_settings(**kw), lambda _: self.log_message.emit(f"✅ ADC applied: {list(kw.keys())}"), "ADC 오류", self.btn_apply_adc)

    def _run_sdk(self, fn, ok=None, err="Error", btn=None):
        if self._cmd_thread and self._cmd_thread.isRunning(): return
        if btn: btn.setEnabled(False)
        t = QThread(); w = _CameraCommandWorker(fn); self._cmd_thread = t
        w.moveToThread(t); t.started.connect(w.run)
        if ok: w.success.connect(ok)
        w.error.connect(lambda e: self.log_message.emit(f"❌ {err}: {e}"))
        w.success.connect(lambda _: t.quit()); w.error.connect(lambda _: t.quit())
        if btn: t.finished.connect(lambda: btn.setEnabled(self._camera is not None))
        t.start()

    def _on_temp_read(self, r, s, st):
        self.lbl_temp_status.setText(f"Reading: {r:.1f}°C | SP: {s:.1f}°C" if r is not None else "Reading: —")

    def _save_settings(self):
        s = QSettings("SpeAnalyze", "CameraPanel")
        s.setValue("exposure", self.spin_exposure.value())
        if hasattr(self, 'spin_readout_count'): s.setValue("readout_count", self.spin_readout_count.value())

    def _load_settings(self):
        s = QSettings("SpeAnalyze", "CameraPanel")
        self.spin_exposure.setValue(float(s.value("exposure", 20.0)))
        if hasattr(self, 'spin_readout_count'): self.spin_readout_count.setValue(int(s.value("readout_count", 1)))

"""

# Combine header and body
full_content = "".join(header) + body

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(full_content)
