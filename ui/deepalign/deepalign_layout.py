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

import os
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QFileDialog,
    QGroupBox,
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

from ui.viewer_v2.deepalign_adapter import DeepAlignViewerV2Adapter


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

        self.cam_viewer = DeepAlignViewerV2Adapter()
        self.cam_viewer.set_external_render_control(True)
        host.setCentralWidget(self.cam_viewer)

        return host

    def _create_cam_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        editor_combo_style = """
            QComboBox {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }
            QComboBox:hover {
                border-color: #22d3ee;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
        """
        editor_spin_style = """
            QAbstractSpinBox {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }
            QAbstractSpinBox:hover {
                border-color: #22d3ee;
            }
            QAbstractSpinBox::up-button,
            QAbstractSpinBox::down-button {
                width: 16px;
                background: #0f172a;
                border-left: 1px solid #334155;
            }
            QAbstractSpinBox::up-button:hover,
            QAbstractSpinBox::down-button:hover {
                background: #172036;
            }
        """
        editor_line_style = """
            QLineEdit {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
                font-size: 12px;
                font-weight: 800;
            }
            QLineEdit:hover {
                border-color: #22d3ee;
            }
        """

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
        self.cb_vendor.setStyleSheet(editor_combo_style)
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
        self.spin_exposure.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_exposure.setStyleSheet(editor_spin_style)
        self.btn_apply_exp = self._style_btn("APPLY", "#14b8a6")
        self.btn_apply_exp.setMinimumWidth(86)
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
        self.spin_fps.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_fps.setStyleSheet(editor_spin_style)
        self.btn_apply_fps = self._style_btn("APPLY", "#14b8a6")
        self.btn_apply_fps.setMinimumWidth(86)
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
            cb.setStyleSheet(editor_combo_style)
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
        self.spin_temp.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_temp.setStyleSheet(editor_spin_style)
        self.btn_apply_temp = self._style_btn("SET", "#14b8a6")
        self.btn_apply_temp.setMinimumWidth(72)
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
        self.spin_frame_to_save.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.spin_frame_to_save.setStyleSheet(editor_spin_style)
        save_count_row.addWidget(lbl_count)
        save_count_row.addWidget(self.spin_frame_to_save, 1)
        sl.addLayout(save_count_row)

        self.edit_file_base = QLineEdit("Capture")
        self.edit_folder = QLineEdit("Live_Captures")
        self.btn_browse_folder = QPushButton("...")
        self.btn_browse_folder.setFixedWidth(34)
        self.btn_browse_folder.setToolTip("저장 폴더 선택")
        self.check_inc_name = QCheckBox("Increment File Name")
        self.check_add_date = QCheckBox("Add Date")
        self.check_add_date.setChecked(True)
        self.check_add_time = QCheckBox("Add Time")
        self.check_add_time.setChecked(True)
        for chk in (self.check_inc_name, self.check_add_date, self.check_add_time):
            chk.setStyleSheet("""
                QCheckBox {
                    color: #e2e8f0;
                    font-size: 13px;
                    font-weight: 700;
                    spacing: 8px;
                }
                QCheckBox::indicator {
                    width: 16px;
                    height: 16px;
                    border-radius: 3px;
                    border: 1px solid #64748b;
                    background: #020617;
                }
                QCheckBox::indicator:hover {
                    border-color: #22d3ee;
                }
                QCheckBox::indicator:checked {
                    border-color: #22d3ee;
                    background: #22d3ee;
                }
            """)

        self.cb_date_fmt = QComboBox(); self.cb_date_fmt.addItems(["YYYY-Month-DD", "YYYY-MM-DD"])
        self.cb_time_fmt = QComboBox(); self.cb_time_fmt.addItems(["hh:mm:ss (24h)", "hh:mm:ss (12h)"])
        self.cb_place = QComboBox(); self.cb_place.addItems(["Suffix", "Prefix"])
        for cb in (self.cb_date_fmt, self.cb_time_fmt, self.cb_place):
            cb.setStyleSheet("color: #14b8a6; font-size: 12px; font-weight: bold;")

        self.btn_browse_folder.setStyleSheet(
            """
            QPushButton {
                background: #0f172a;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 4px;
                font-weight: 900;
            }
            QPushButton:hover {
                border-color: #22d3ee;
                color: #22d3ee;
            }
        """
        )

        self.edit_file_base.setStyleSheet(editor_line_style)
        self.edit_folder.setStyleSheet(editor_line_style)

        row_folder = QHBoxLayout()
        lbl_folder = QLabel("Save In:")
        lbl_folder.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        row_folder.addWidget(lbl_folder)
        row_folder.addWidget(self.edit_folder, 1)
        row_folder.addWidget(self.btn_browse_folder)
        sl.addLayout(row_folder)

        row_name = QHBoxLayout()
        lbl_name = QLabel("File Name:")
        lbl_name.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        row_name.addWidget(lbl_name)
        row_name.addWidget(self.edit_file_base, 1)
        sl.addLayout(row_name)

        naming_box = QGroupBox("Naming Options")
        naming_box.setStyleSheet(
            """
            QGroupBox {
                color: #e2e8f0;
                font-size: 13px;
                font-weight: 800;
                background: #020817;
                border: 1px solid #475569;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 6px;
                color: #e2e8f0;
                background: #020817;
            }
            QGroupBox QLabel {
                color: #cbd5e1;
                font-size: 12px;
                font-weight: 700;
            }
            QGroupBox QComboBox {
                background: #0b1220;
                color: #22d3ee;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 24px;
            }
            QGroupBox QComboBox:disabled {
                color: #64748b;
                border-color: #334155;
                background: #0a0f1a;
            }
        """
        )
        nl = QVBoxLayout(naming_box)
        nl.setSpacing(6)
        nl.setContentsMargins(10, 10, 10, 10)

        nl.addWidget(self.check_inc_name)
        nl.addWidget(self.check_add_date)
        row_df = QHBoxLayout(); row_df.addWidget(QLabel("Date Format:")); row_df.addWidget(self.cb_date_fmt, 1)
        row_df.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        nl.addLayout(row_df)
        nl.addWidget(self.check_add_time)
        row_tf = QHBoxLayout(); row_tf.addWidget(QLabel("Time Format:")); row_tf.addWidget(self.cb_time_fmt, 1)
        row_tf.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        nl.addLayout(row_tf)
        row_pl = QHBoxLayout(); row_pl.addWidget(QLabel("Place Date/Time:")); row_pl.addWidget(self.cb_place, 1)
        row_pl.itemAt(0).widget().setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        nl.addLayout(row_pl)
        sl.addWidget(naming_box)

        self.lbl_save_preview = QLabel("Example File Name: Capture_2026-May-10_00_00_00.spe")
        self.lbl_save_full = QLabel("Full Path: Live_Captures/Capture_2026-May-10_00_00_00.spe")
        self.lbl_save_preview.setWordWrap(True)
        self.lbl_save_full.setWordWrap(True)
        self.lbl_save_preview.setStyleSheet("color: #4ecdc4; font-size: 12px; font-weight: 900;")
        self.lbl_save_full.setStyleSheet("color: #7dd3fc; font-size: 12px; font-weight: 900;")
        sl.addWidget(self.lbl_save_preview)
        sl.addWidget(self.lbl_save_full)

        for widget in [self.edit_file_base, self.edit_folder]:
            widget.textChanged.connect(self._update_save_preview)
        for widget in [self.check_inc_name, self.check_add_date, self.check_add_time]:
            widget.toggled.connect(self._update_save_preview)
            widget.toggled.connect(self._update_save_control_state)
        for widget in [self.cb_date_fmt, self.cb_time_fmt, self.cb_place]:
            widget.currentTextChanged.connect(self._update_save_preview)
        self.btn_browse_folder.clicked.connect(self._on_browse_save_folder)

        self._update_save_control_state()
        self._update_save_preview()
        p_lay.addWidget(save_grp)

        p_lay.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll)
        return page

    def _on_browse_save_folder(self):
        current_dir = self.edit_folder.text().strip() or os.getcwd()
        selected = QFileDialog.getExistingDirectory(self, "Save In", current_dir)
        if selected:
            self.edit_folder.setText(selected)

    def _update_save_control_state(self):
        has_date = self.check_add_date.isChecked()
        has_time = self.check_add_time.isChecked()
        self.cb_date_fmt.setEnabled(has_date)
        self.cb_time_fmt.setEnabled(has_time)
        self.cb_place.setEnabled(has_date or has_time)

    def _update_save_preview(self):
        now = datetime.now()
        base = self.edit_file_base.text().strip() or "Capture"
        folder = self.edit_folder.text().strip() or "Live_Captures"
        tokens = []

        if self.check_add_date.isChecked():
            if self.cb_date_fmt.currentText() == "YYYY-MM-DD":
                tokens.append(now.strftime("%Y-%m-%d"))
            else:
                tokens.append(now.strftime("%Y-%B-%d"))

        if self.check_add_time.isChecked():
            if self.cb_time_fmt.currentText() == "hh:mm:ss (12h)":
                tokens.append(now.strftime("%I_%M_%S%p"))
            else:
                tokens.append(now.strftime("%H_%M_%S"))

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
        full_path = os.path.normpath(os.path.join(folder, full_name))
        self.lbl_save_preview.setText(f"Example File Name: {full_name}")
        self.lbl_save_full.setText(f"Full Path: {full_path}")

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
        self.btn_acquire = self._dash_btn("ACQUIRE", "SAVE", "#e11d48")
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

