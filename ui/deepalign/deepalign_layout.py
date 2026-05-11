"""DeepAlign UI 구성 파일.

이 파일은 DeepAlign 전용 페이지 생성 코드를 담고 있습니다.
주요 역할은 다음과 같습니다.
- 아이콘 사이드바와 스택 페이지 외형 생성
- 카메라 설정 페이지 생성
- 분석 페이지 생성
- 도킹 viewer 작업영역과 마스터 커맨드 바 생성
- 카메라 capability에 따라 보이기/숨기기 되는 UI 보조 처리
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.image_viewer import ImageViewer


class LayoutBuilderMixin:
    def _create_icon_sidebar(self):
        sidebar = QFrame()
        sidebar.setFixedWidth(65)
        sidebar.setStyleSheet("background-color: #020617; border-right: 1px solid #1e293b;")
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 25, 0, 25)
        lay.setSpacing(25)

        icons = [
            ("📷", "#94a3b8"),
            ("🪞", "#38bdf8"),
            ("🔍", "#fbbf24"),
            ("🎯", "#ef4444"),
            ("📊", "#10b981"),
        ]
        self.sidebar_btns = []
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        for i, (icon, color) in enumerate(icons):
            btn = QPushButton(icon)
            btn.setFixedSize(45, 45)
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"""
                QPushButton {{ background: transparent; color: {color}; font-size: 24px;
                               border: none; border-radius: 12px; }}
                QPushButton:hover {{ background: #1e293b; color: #f8fafc; }}
                QPushButton:checked {{ background: #1e293b; color: #22d3ee;
                                       border: 1px solid #22d3ee; }}
            """
            )
            btn.clicked.connect(lambda _, idx=i: self._on_tab_changed(idx))
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
            self.sidebar_btns.append(btn)
            self.btn_group.addButton(btn, i)

        self.sidebar_btns[0].setChecked(True)
        lay.addStretch()
        return sidebar

    def _on_tab_changed(self, idx: int):
        self.central_stack.setCurrentIndex(idx)
        if hasattr(self, "master_btn_stack"):
            self.master_btn_stack.setCurrentIndex(min(idx, self.master_btn_stack.count() - 1))

    def _wrap_panel(self, panel: QWidget) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.addWidget(panel)

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _create_docking_workspace(self) -> QMainWindow:
        host = QMainWindow()
        host.setObjectName("deepAlignDockHost")
        host.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks |
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.AnimatedDocks
        )
        host.setStyleSheet("QMainWindow { background: #060d19; }")

        self.cam_viewer = ImageViewer()
        self.cam_viewer.set_external_render_control(True)
        host.setCentralWidget(self.cam_viewer)

        roi_dock = QDockWidget("ROI LIST", host)
        roi_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        roi_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        roi_body = QWidget()
        roi_lay = QVBoxLayout(roi_body)
        roi_lay.setContentsMargins(8, 8, 8, 8)
        roi_lay.setSpacing(6)
        roi_row = QHBoxLayout()
        roi_row.setContentsMargins(0, 0, 0, 0)
        roi_row.setSpacing(6)
        lbl_roi = QLabel("ROI LIST")
        lbl_roi.setStyleSheet("color:#8ca8cc;font-size:12px;font-weight:900;")
        btn_roi_del = self._style_btn("DEL", "#64748b")
        btn_roi_all = self._style_btn("ALL", "#64748b")
        btn_roi_del.setFixedHeight(24)
        btn_roi_all.setFixedHeight(24)
        roi_row.addWidget(lbl_roi)
        roi_row.addStretch()
        roi_row.addWidget(btn_roi_del)
        roi_row.addWidget(btn_roi_all)
        roi_lay.addLayout(roi_row)

        self.roi_list = QListWidget()
        self.roi_list.setMinimumWidth(260)
        self.roi_list.setStyleSheet("background:#020b17;border:1px solid #123252;color:#8ca8cc;")
        self.roi_list.itemClicked.connect(self._on_roi_item_clicked)
        roi_lay.addWidget(self.roi_list)
        roi_dock.setWidget(roi_body)
        host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, roi_dock)

        btn_roi_del.clicked.connect(self._on_roi_del_clicked)
        btn_roi_all.clicked.connect(self._on_roi_clear_clicked)

        host.resizeDocks([roi_dock], [280], Qt.Orientation.Horizontal)
        return host

    def _create_cam_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        p_lay = QVBoxLayout(container)
        p_lay.setContentsMargins(10, 10, 10, 10)
        p_lay.setSpacing(8)

        conn_grp = self._make_section("CAMERA DEVICE CONNECTION", "#64748b")
        conn_grp.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        conn_grp.content_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        cl = QVBoxLayout(conn_grp.content_widget)
        cl.setSpacing(4)
        cl.setContentsMargins(6, 6, 6, 6)

        vg_frame = QFrame()
        vg_frame.setFixedHeight(28)
        vg_frame.setStyleSheet("QFrame { border: 1px solid #1e293b; }")
        vg = QHBoxLayout(vg_frame)
        vg.setContentsMargins(0, 0, 0, 0)
        vg.setSpacing(0)
        lbl_vendor = QLabel(" VENDOR:")
        lbl_vendor.setFixedWidth(80)
        lbl_vendor.setStyleSheet(
            "color: #94a3b8; font-size: 12px; font-weight: bold;"
            " border-right: 1px solid #1e293b; padding: 0 6px;"
            " background: rgba(30,41,59,0.2);"
        )
        vg.addWidget(lbl_vendor)
        self.cb_vendor = QComboBox()
        self.cb_vendor.addItems(["HIKVISION", "Picam", "Simulation"])
        self.cb_vendor.setStyleSheet(
            "color: #14b8a6; font-size: 12px; font-weight: bold; border: none; padding: 2px 6px;"
        )
        self.cb_vendor.setCurrentIndex(2)
        vg.addWidget(self.cb_vendor, 1)
        cl.addWidget(vg_frame)

        self.cam_list = QListWidget()
        self.cam_list.setFixedHeight(36)
        self.cam_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.cam_list.setStyleSheet(
            "background: #020617; border: 1px solid #1e293b; color: #94a3b8; font-size: 11px;"
        )
        cl.addWidget(self.cam_list)

        self.btn_scan = self._style_btn("SCAN", "#64748b")
        self.btn_scan.setFixedHeight(24)
        cl.addWidget(self.btn_scan)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(6)
        self.btn_connect = self._style_btn("CONNECT", "#14b8a6")
        self.btn_disconnect = self._style_btn("DISCONNECT", "#ef4444")
        self.btn_connect.setFixedHeight(24)
        self.btn_disconnect.setFixedHeight(24)
        conn_row.addWidget(self.btn_connect)
        conn_row.addWidget(self.btn_disconnect)
        cl.addLayout(conn_row)
        p_lay.addWidget(conn_grp)

        acq_grp = self._make_section("IMAGE ACQUISITION", "#22d3ee")
        al = QVBoxLayout(acq_grp.content_widget)
        al.setSpacing(8)
        al.setContentsMargins(10, 10, 10, 10)

        exp_row = QHBoxLayout()
        lbl_exp = QLabel("Exposure (ms):")
        lbl_exp.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        self.spin_exposure = QDoubleSpinBox()
        self.spin_exposure.setRange(0.01, 1_000_000.0)
        self.spin_exposure.setDecimals(2)
        self.spin_exposure.setValue(20.0)
        self.spin_exposure.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")
        self.btn_apply_exp = self._style_btn("APPLY", "#14b8a6")
        exp_row.addWidget(lbl_exp)
        exp_row.addWidget(self.spin_exposure, 1)
        exp_row.addWidget(self.btn_apply_exp)
        al.addLayout(exp_row)

        self.sec_fps = QFrame()
        fps_lay = QHBoxLayout(self.sec_fps)
        fps_lay.setContentsMargins(0, 0, 0, 0)
        self.check_fps_lock = QCheckBox("Lock FPS")
        self.check_fps_lock.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self.spin_fps = QDoubleSpinBox()
        self.spin_fps.setRange(0.1, 1000.0)
        self.spin_fps.setValue(30.0)
        self.spin_fps.setSuffix(" fps")
        self.spin_fps.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")
        self.btn_apply_fps = self._style_btn("APPLY", "#14b8a6")
        fps_lay.addWidget(self.check_fps_lock)
        fps_lay.addWidget(self.spin_fps, 1)
        fps_lay.addWidget(self.btn_apply_fps)
        al.addWidget(self.sec_fps)
        p_lay.addWidget(acq_grp)

        self.sec_adc = self._make_section("ADC SETTINGS", "#22d3ee")
        adl = QVBoxLayout(self.sec_adc.content_widget)
        adl.setSpacing(6)
        adl.setContentsMargins(10, 10, 10, 10)
        self.cb_adc_quality = QComboBox()
        self.cb_adc_speed = QComboBox()
        self.cb_adc_gain = QComboBox()
        self.cb_adc_bit = QComboBox()
        for lbl_text, cb in [
            ("Quality:", self.cb_adc_quality),
            ("Speed:", self.cb_adc_speed),
            ("Gain:", self.cb_adc_gain),
            ("Bit Depth:", self.cb_adc_bit),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
            cb.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")
            row.addWidget(lbl)
            row.addWidget(cb, 1)
            adl.addLayout(row)
        self.btn_apply_adc = self._style_btn("APPLY ADC", "#14b8a6")
        adl.addWidget(self.btn_apply_adc)
        p_lay.addWidget(self.sec_adc)

        self.sec_temp = self._make_section("TEMPERATURE", "#22d3ee")
        tl = QVBoxLayout(self.sec_temp.content_widget)
        tl.setSpacing(6)
        tl.setContentsMargins(10, 10, 10, 10)
        trow = QHBoxLayout()
        lbl_temp = QLabel("Setpoint (C):")
        lbl_temp.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(-100.0, 50.0)
        self.spin_temp.setValue(-70.0)
        self.spin_temp.setDecimals(2)
        self.spin_temp.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")
        self.btn_apply_temp = self._style_btn("SET", "#14b8a6")
        trow.addWidget(lbl_temp)
        trow.addWidget(self.spin_temp, 1)
        trow.addWidget(self.btn_apply_temp)
        tl.addLayout(trow)
        self.lbl_temp_read = QLabel("Reading: ---")
        self.lbl_temp_set = QLabel("Setpoint: ---")
        self.lbl_temp_state = QLabel("Status: ---")
        for item in (self.lbl_temp_read, self.lbl_temp_set, self.lbl_temp_state):
            item.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
            tl.addWidget(item)
        p_lay.addWidget(self.sec_temp)

        save_grp = self._make_section("SAVE", "#22d3ee")
        sl = QVBoxLayout(save_grp.content_widget)
        sl.setSpacing(8)
        sl.setContentsMargins(10, 10, 10, 10)

        save_count_row = QHBoxLayout()
        lbl_count = QLabel("Frame To Save:")
        lbl_count.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        self.spin_frame_to_save = QSpinBox()
        self.spin_frame_to_save.setRange(1, 100000)
        self.spin_frame_to_save.setValue(10)
        self.spin_frame_to_save.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")
        save_count_row.addWidget(lbl_count)
        save_count_row.addWidget(self.spin_frame_to_save, 1)
        sl.addLayout(save_count_row)

        self.edit_file_base = QLineEdit("Capture")
        self.edit_folder = QLineEdit("C:/Data/Captures")
        self.check_inc_name = QCheckBox("Increment File Name")
        self.check_add_date = QCheckBox("Add Date")
        self.check_add_date.setChecked(True)
        self.check_add_time = QCheckBox("Add Time")
        self.check_add_time.setChecked(True)
        for chk in (self.check_inc_name, self.check_add_date, self.check_add_time):
            chk.setStyleSheet("color: #94a3b8; font-size: 12px;")

        self.cb_date_fmt = QComboBox(); self.cb_date_fmt.addItems(["YYYY-Month-DD", "YYYY-MM-DD"])
        self.cb_time_fmt = QComboBox(); self.cb_time_fmt.addItems(["hh:mm:ss (24h)", "hh:mm:ss (12h)"])
        self.cb_place = QComboBox(); self.cb_place.addItems(["Suffix", "Prefix"])
        for cb in (self.cb_date_fmt, self.cb_time_fmt, self.cb_place):
            cb.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")

        for lbl_text, widget in [("File Name:", self.edit_file_base), ("Folder:", self.edit_folder)]:
            row = QHBoxLayout()
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
            widget.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")
            row.addWidget(lbl)
            row.addWidget(widget, 1)
            sl.addLayout(row)

        sl.addWidget(self.check_inc_name)
        sl.addWidget(self.check_add_date)
        row_df = QHBoxLayout(); row_df.addWidget(QLabel("Date Format:")); row_df.addWidget(self.cb_date_fmt, 1)
        row_df.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        sl.addLayout(row_df)
        sl.addWidget(self.check_add_time)
        row_tf = QHBoxLayout(); row_tf.addWidget(QLabel("Time Format:")); row_tf.addWidget(self.cb_time_fmt, 1)
        row_tf.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        sl.addLayout(row_tf)
        row_pl = QHBoxLayout(); row_pl.addWidget(QLabel("Place Date/Time:")); row_pl.addWidget(self.cb_place, 1)
        row_pl.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        sl.addLayout(row_pl)

        self.lbl_save_preview = QLabel("Preview: Capture_2026-May-10_00:00:00.spe")
        self.lbl_save_full = QLabel("Full Path: C:/Data/Captures/Capture_2026-May-10_00:00:00.spe")
        self.lbl_save_preview.setStyleSheet("color: #4ecdc4; font-size: 12px; font-weight: 900;")
        self.lbl_save_full.setStyleSheet("color: #7dd3fc; font-size: 12px; font-weight: 900;")
        sl.addWidget(self.lbl_save_preview)
        sl.addWidget(self.lbl_save_full)

        for widget in [self.edit_file_base, self.edit_folder]:
            widget.textChanged.connect(self._update_save_preview)
        for widget in [self.check_inc_name, self.check_add_date, self.check_add_time]:
            widget.toggled.connect(self._update_save_preview)
        for widget in [self.cb_date_fmt, self.cb_time_fmt, self.cb_place]:
            widget.currentTextChanged.connect(self._update_save_preview)
        self._update_save_preview()
        p_lay.addWidget(save_grp)

        p_lay.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _update_save_preview(self):
        now = datetime.now()
        base = self.edit_file_base.text().strip() or "Capture"
        folder = self.edit_folder.text().strip() or "C:/Data/Captures"
        tokens = []

        if self.check_add_date.isChecked():
            if self.cb_date_fmt.currentText() == "YYYY-MM-DD":
                tokens.append(now.strftime("%Y-%m-%d"))
            else:
                tokens.append(now.strftime("%Y-%b-%d"))

        if self.check_add_time.isChecked():
            if self.cb_time_fmt.currentText() == "hh:mm:ss (12h)":
                tokens.append(now.strftime("%I:%M:%S%p"))
            else:
                tokens.append(now.strftime("%H:%M:%S"))

        if self.check_inc_name.isChecked():
            tokens.append("0001")

        if tokens:
            if self.cb_place.currentText() == "Prefix":
                filename = f"{'_'.join(tokens)}_{base}"
            else:
                filename = f"{base}_{'_'.join(tokens)}"
        else:
            filename = base

        full_name = f"{filename}.spe"
        self.lbl_save_preview.setText(f"Preview: {full_name}")
        self.lbl_save_full.setText(f"Full Path: {folder}/{full_name}")

    def _apply_camera_capabilities(self, caps):
        has_fps = bool(caps and getattr(caps, "has_fps_control", False))
        has_adc = bool(caps and getattr(caps, "has_adc", False))
        has_temp = bool(caps and getattr(caps, "has_temperature", False))

        self.sec_fps.setVisible(has_fps)
        self.sec_adc.setVisible(has_adc)
        self.sec_temp.setVisible(has_temp)

        if has_adc:
            self.cb_adc_quality.clear()
            self.cb_adc_speed.clear()
            self.cb_adc_gain.clear()
            self.cb_adc_bit.clear()

            self.cb_adc_quality.addItems(getattr(caps, "adc_quality_options", []) or ["High Capacity", "Low Noise"])
            self.cb_adc_speed.addItems(getattr(caps, "adc_speed_options", []) or ["100kHz", "1MHz"])
            self.cb_adc_gain.addItems(getattr(caps, "adc_gain_options", []) or ["1x", "2x"])
            self.cb_adc_bit.addItems(getattr(caps, "adc_bit_depth_options", []) or ["16bit", "12bit"])

    def _create_analysis_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        container = QWidget()
        p_lay = QVBoxLayout(container)
        p_lay.setContentsMargins(10, 10, 10, 10)
        p_lay.setSpacing(12)

        sum_grp = self._make_section("📊 ANALYSIS SUMMARY", "#10b981")
        sl = QVBoxLayout(sum_grp.content_widget)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(8)
        metrics = [("PEAK INTENSITY", "---"), ("FWHM (px)", "---"), ("SNR", "---")]
        grid = QFrame(); grid.setStyleSheet("border: 1px solid #1e293b;")
        gl = QGridLayout(grid); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(0)
        for i, (metric, value) in enumerate(metrics):
            gl.addWidget(self._grid_lbl(f" {metric}"), i, 0)
            vv = QLabel(value)
            vv.setStyleSheet("color: #10b981; font-size: 12px; font-weight: 900; padding: 6px;")
            gl.addWidget(vv, i, 1)
        sl.addWidget(grid)
        p_lay.addWidget(sum_grp)
        p_lay.addStretch()

        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _create_master_bar(self):
        bar = QFrame()
        bar.setFixedHeight(75)
        bar.setStyleSheet("background-color: #020617; border-top: none;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(15, 10, 15, 5)
        lay.setSpacing(25)

        self.master_btn_stack = QStackedWidget()
        self.master_btn_stack.setFixedSize(380, 45)

        cam_w = QWidget(); cbl = QHBoxLayout(cam_w); cbl.setContentsMargins(0, 0, 0, 0); cbl.setSpacing(8)
        self.btn_snap = self._dash_btn("SNAP", "", "#3b82f6")
        self.btn_live_air = self._dash_btn("LIVE", "ON AIR", "#14b8a6")
        self.btn_acquire = self._dash_btn("ACQUIRE", "RECORDING", "#e11d48")
        self.btn_stop_main = self._dash_btn("STOP", "", "#ef4444")
        for button in (self.btn_snap, self.btn_live_air, self.btn_acquire, self.btn_stop_main):
            cbl.addWidget(button)
        self.master_btn_stack.addWidget(cam_w)

        mir_w = QWidget(); mbl = QHBoxLayout(mir_w); mbl.setContentsMargins(0, 0, 0, 0); mbl.setSpacing(8)
        for title, sub, color in [("ZERO ALL", "ALL AXIS", "#38bdf8"), ("RESET", "", "#64748b"), ("STOP", "EMERGENCY", "#ef4444")]:
            mbl.addWidget(self._dash_btn(title, sub, color))
        self.master_btn_stack.addWidget(mir_w)

        af_w = QWidget(); abl = QHBoxLayout(af_w); abl.setContentsMargins(0, 0, 0, 0); abl.setSpacing(8)
        for title, sub, color in [("RUN AF", "SEARCH", "#fbbf24"), ("ABORT", "", "#ef4444"), ("SET Z", "BASE", "#3b82f6")]:
            abl.addWidget(self._dash_btn(title, sub, color))
        self.master_btn_stack.addWidget(af_w)

        al_w = QWidget(); albl = QHBoxLayout(al_w); albl.setContentsMargins(0, 0, 0, 0); albl.setSpacing(8)
        for title, sub, color in [("ENABLE", "ALL", "#4ecdc4"), ("CALC", "KINEM.", "#aa7acc"), ("MOVE", "EXECUTE", "#ef4444"), ("STOP", "ALL", "#64748b")]:
            albl.addWidget(self._dash_btn(title, sub, color))
        self.master_btn_stack.addWidget(al_w)

        an_w = QWidget()
        lbl_an = QLabel("ANALYSIS COMMANDS")
        lbl_an.setStyleSheet("color: #10b981; font-weight: 900;")
        QHBoxLayout(an_w).addWidget(lbl_an)
        self.master_btn_stack.addWidget(an_w)

        lay.addWidget(self.master_btn_stack)

        prog_lay = QVBoxLayout(); prog_lay.setSpacing(6); prog_lay.setContentsMargins(10, 5, 10, 5)
        top_row = QHBoxLayout()
        self.lbl_frame_info = QLabel("FRAME: <font color='#f8fafc'>— / —</font>")
        self.lbl_frame_info.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; border: none;")
        self.lbl_times = QLabel("ELAPSED: <font color='#f8fafc'>00:00:00</font> | REMAIN: <font color='#f8fafc'>00:00:00</font> | ETA: <font color='#f8fafc'>00:00:00</font>")
        self.lbl_times.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; border: none;")
        top_row.addWidget(self.lbl_frame_info); top_row.addStretch(); top_row.addWidget(self.lbl_times)
        prog_lay.addLayout(top_row)

        self.prog_container = QFrame(); self.prog_container.setFixedHeight(22)
        self.prog_container.setStyleSheet("background: #0f172a; border-radius: 11px; border: 1px solid #1e293b;")

        self.prog_grid = QGridLayout(self.prog_container)
        self.prog_grid.setContentsMargins(0, 0, 0, 0)

        self.prog_fill = QFrame(); self.prog_fill.setFixedHeight(22)
        self.prog_fill.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #e11d48,stop:1 #fb7185); border-radius: 11px;"
        )
        self.prog_grid.addWidget(self.prog_fill, 0, 0)

        self.lbl_prog_text = QLabel("0% COMPLETE")
        self.lbl_prog_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_prog_text.setStyleSheet("color: white; font-size: 10px; font-weight: 900; background: transparent; border: none;")
        self.prog_grid.addWidget(self.lbl_prog_text, 0, 0)

        self.prog_spacer = QWidget()
        self.prog_grid.addWidget(self.prog_spacer, 0, 1)
        self.prog_grid.setColumnStretch(0, 0)
        self.prog_grid.setColumnStretch(1, 100)

        prog_lay.addWidget(self.prog_container)
        lay.addLayout(prog_lay, 1)

        tel = QHBoxLayout(); tel.setSpacing(20)
        for label, val in [("DROPPED", "0"), ("WRITE RATE", "--- MB/s"), ("STORAGE", "--- Free"), ("BUFFER", "---")]:
            vbox = QVBoxLayout(); vbox.setSpacing(2)
            ll = QLabel(label); ll.setStyleSheet("color: #64748b; font-size: 9px; font-weight: 900; border: none;")
            vv = QLabel(val); vv.setStyleSheet("color: #14b8a6; font-size: 11px; font-weight: 900; border: none;")
            vbox.addWidget(ll); vbox.addWidget(vv)
            tel.addLayout(vbox)
        lay.addLayout(tel)
        return bar

