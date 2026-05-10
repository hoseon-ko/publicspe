
from PyQt6.QtWidgets import (QDoubleSpinBox, QGroupBox, QSplitter, QTextEdit, QWidget, 
                             QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, 
                             QPushButton, QComboBox, QFrame, QSlider, QSpinBox)
from PyQt6.QtCore import Qt, pyqtSlot, pyqtSignal, QRect
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont
import logging
import numpy as np

from ui.live.acs_stage_panel import AcsStagePanel
from core.motor.kinematic_calc import KinematicCalc
from ui.kinematic.kinematic_3d_view import Kinematic3DWidget

log = logging.getLogger(__name__)

# --- Reusable Styles ---
STYLE_BTN_PRIMARY = """
    QPushButton { 
        background-color: #00f2ff; color: #0d121f; font-weight: 900; 
        height: 32px; border-radius: 4px; border: none;
    }
    QPushButton:hover { background-color: #ffffff; }
    QPushButton:pressed { background-color: #00c2cc; }
"""
STYLE_BTN_SECONDARY = """
    QPushButton { 
        background-color: transparent; color: #00f2ff; font-weight: bold; 
        height: 28px; border: 1px solid #00f2ff; border-radius: 4px;
    }
    QPushButton:hover { background-color: rgba(0, 242, 255, 0.1); }
"""
STYLE_INPUT = """
    QDoubleSpinBox, QSpinBox { 
        background: #080b14; border: 1px solid #1e293b; color: #e2e8f0; 
        padding: 4px; border-radius: 3px; 
    }
    QDoubleSpinBox:hover, QSpinBox:hover { border-color: #334155; }
"""
STYLE_GROUP = """
    QGroupBox { 
        color: #94a3b8; font-weight: bold; font-size: 11px;
        border: 1px solid #1e293b; border-radius: 6px; margin-top: 15px; padding-top: 10px;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
"""
STYLE_SLIDER_OVERLAY = """
    QSlider::groove:horizontal { border: 1px solid rgba(0, 242, 255, 0.3); height: 8px; background: rgba(8, 11, 20, 0.5); border-radius: 4px; }
    QSlider::handle:horizontal { background: #00f2ff; width: 22px; height: 22px; margin: -7px 0; border-radius: 11px; border: 2px solid white; }
"""

class WorkspaceMapWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 400)
        self.matrix = None
        self.range_x = (-15, 15)
        self.range_y = (-15, 15)
        self.current_pos = (0, 0)
        self.axis_name = "XY"
        self.axis_labels = ("Tx", "Ty") # 가로축, 세로축 라벨 추가
        self.margin = 30
        
    def set_data(self, matrix, rx, ry, cx, cy, axis_name="XY", labels=("Tx", "Ty")):
        self.matrix = matrix
        self.range_x = rx if rx[0] != rx[1] else (rx[0]-1, rx[1]+1)
        self.range_y = ry if ry[0] != ry[1] else (ry[0]-1, ry[1]+1)
        self.current_pos = (cx, cy)
        self.axis_name = axis_name
        self.axis_labels = labels
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#0f172a"))
        painter.setPen(QPen(QColor("#1e293b"), 1))
        for i in range(11):
            x, y = int(i * w / 10), int(i * h / 10)
            painter.drawLine(x, 0, x, h); painter.drawLine(0, y, w, y)

        m = self.margin
        drawable_w = w - 2*m; drawable_h = h - 2*m
        painter.setFont(QFont("Inter", 10, QFont.Weight.Bold)); painter.setPen(QColor("#64748b"))
        
        # Axis Labels
        painter.drawText(w//2 - 20, h - 5, f"Axis: {self.axis_labels[0]}")
        painter.save()
        painter.translate(15, h//2 + 20)
        painter.rotate(-90)
        painter.drawText(0, 0, f"Axis: {self.axis_labels[1]}")
        painter.restore()

        painter.setFont(QFont("Inter", 11, QFont.Weight.Bold)); painter.setPen(QColor("#ffffff"))
        painter.drawText(m, h - 10, f"{self.range_x[0]:.1f}")
        painter.drawText(w - m - 40, h - 10, f"{self.range_x[1]:.1f}")
        painter.drawText(25, m + 15, f"{self.range_y[1]:.1f}")
        painter.drawText(25, h - m, f"{self.range_y[0]:.1f}")

        if self.matrix is not None:
            res_y, res_x = self.matrix.shape; dx, dy = drawable_w / res_x, drawable_h / res_y
            painter.setPen(QPen(QColor("#00f2ff"), 1)); painter.setBrush(QBrush(QColor(0, 242, 255, 45)))
            for i in range(res_y):
                for j in range(res_x):
                    if self.matrix[i, j]: painter.drawRect(int(m + j*dx), int(m + (res_y-1-i)*dy), int(dx)+1, int(dy)+1)
        elif "1D Search" in self.axis_name:
            painter.setPen(QPen(QColor(0, 255, 136, 40), 16, cap=Qt.PenCapStyle.RoundCap)); center_y = h // 2
            painter.drawLine(m, center_y, w - m, center_y)
            painter.setPen(QPen(QColor("#00ff88"), 3, cap=Qt.PenCapStyle.RoundCap)); painter.drawLine(m, center_y, w - m, center_y)
            painter.setFont(QFont("Inter", 14, QFont.Weight.Bold)); painter.setPen(QColor("#00ff88"))
            painter.drawText(w//2 - 70, center_y - 25, f"1D RANGE ({self.axis_name.split(' ')[0]})")

        rx_range, ry_range = self.range_x[1] - self.range_x[0], self.range_y[1] - self.range_y[0]
        if rx_range != 0 and ry_range != 0:
            cx = m + (self.current_pos[0] - self.range_x[0]) / rx_range * drawable_w
            cy = h - m - (self.current_pos[1] - self.range_y[0]) / ry_range * drawable_h
            painter.setBrush(QBrush(QColor(255, 255, 255, 100))); painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(cx)-12, int(cy)-12, 24, 24)
            painter.setBrush(QBrush(Qt.GlobalColor.white)); painter.setPen(QPen(QColor("#00f2ff"), 3))
            painter.drawEllipse(int(cx)-7, int(cy)-7, 14, 14)

class MotorStressBar(QWidget):
    def __init__(self, name, parent=None):
        super().__init__(parent); self.name = name; self.val, self.m_lim, self.p_lim = 0.0, -10.0, 10.0; self.percent = 50.0; self.setFixedHeight(45)
    def set_value(self, val, m_lim, p_lim):
        self.val, self.m_lim, self.p_lim = val, m_lim, p_lim
        total = p_lim - m_lim; self.percent = (val - m_lim) / total * 100 if total != 0 else 50; self.update()
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing); w, h = self.width(), self.height()
        painter.setFont(QFont("Inter", 9, QFont.Weight.Bold)); painter.setPen(QColor("#94a3b8")); painter.drawText(5, 15, self.name)
        painter.setPen(QColor("#ffffff")); painter.drawText(w//2 - 25, 15, f"{self.val:+.2f}")
        bx, bw, by, bh = 5, w - 10, 22, 10
        painter.setBrush(QColor("#1e293b")); painter.setPen(Qt.PenStyle.NoPen); painter.drawRoundedRect(bx, by, bw, bh, 5, 5)
        fill_color = QColor("#00ff88")
        if self.percent > 90 or self.percent < 10: fill_color = QColor("#ff4d4d")
        elif self.percent > 85 or self.percent < 15: fill_color = QColor("#ffcc00")
        cursor_x = bx + int(bw * self.percent / 100); center_x = bx + bw // 2; painter.setBrush(fill_color)
        if cursor_x >= center_x: painter.drawRect(center_x, by, cursor_x - center_x, bh)
        else: painter.drawRect(cursor_x, by, center_x - cursor_x, bh)
        painter.setBrush(Qt.GlobalColor.white); painter.drawEllipse(cursor_x - 3, by - 2, 6, bh + 4)

class KinematicTab(QWidget):
    log_message = pyqtSignal(str); kin_starting = pyqtSignal(); kin_done = pyqtSignal()
    def __init__(self, parent=None, acs_ctrl: AcsStageController = None):
        super().__init__(parent); self.acs_ctrl = acs_ctrl; self.calc = KinematicCalc(); self.acs_panel = None; self.current_1d_range = (0, 0); self._setup_ui()
    def set_acs_ctrl(self, ctrl):
        if self.acs_panel: self.acs_panel.set_controller(ctrl); self.update_analysis()
    def clear_acs_ctrl(self):
        if self.acs_panel: self.acs_panel.set_controller(None)
    def set_shared_cameraera(self, camera): pass
    def clear_shared_cameraera(self): pass
    def stop_polling(self):
        if self.acs_panel: self.acs_panel.stop_polling()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self); main_layout.setContentsMargins(5, 5, 5, 5)
        self.splitter = QSplitter(Qt.Orientation.Horizontal); self.splitter.setStyleSheet("QSplitter::handle { background-color: #1e293b; width: 2px; }")
        self.acs_panel = AcsStagePanel(None, ctrl=self.acs_ctrl); self.acs_panel.log_message.connect(self.log_message.emit); self.splitter.addWidget(self.acs_panel)
        center_widget = QWidget(); center_layout = QVBoxLayout(center_widget)
        map_ctrl_lay = QHBoxLayout(); map_ctrl_lay.addWidget(QLabel("MODE:")); self.combo_plane = QComboBox(); self.combo_plane.addItems(["XY (2D Map)", "XZ (2D Map)", "YZ (2D Map)", "RxRy (2D Map)", "RxRz (2D Map)", "RyRz (2D Map)", "TxRx (2D Map)", "TyRy (2D Map)", "TzRz (2D Map)", "Tx (1D Search)", "Ty (1D Search)", "Tz (1D Search)", "Rx (1D Search)", "Ry (1D Search)", "Rz (1D Search)"]); self.combo_plane.currentIndexChanged.connect(self.update_analysis); map_ctrl_lay.addWidget(self.combo_plane)
        self.lbl_fixed_info = QLabel("Fixed: ..."); self.lbl_fixed_info.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: bold;"); map_ctrl_lay.addWidget(self.lbl_fixed_info); map_ctrl_lay.addStretch(); center_layout.addLayout(map_ctrl_lay)
        map_vbox = QVBoxLayout(); self.map_widget = WorkspaceMapWidget(); map_vbox.addWidget(self.map_widget)
        self.slider_1d = QSlider(Qt.Orientation.Horizontal); self.slider_1d.setStyleSheet(STYLE_SLIDER_OVERLAY); self.slider_1d.setRange(0, 1000); self.slider_1d.setFixedHeight(25); self.slider_1d.valueChanged.connect(self._on_slider_moved_internal)
        self.container_1d = QWidget(); c1d_lay = QHBoxLayout(self.container_1d); c1d_lay.setContentsMargins(30, 0, 30, 0); c1d_lay.addWidget(self.slider_1d); map_vbox.addWidget(self.container_1d)

        self.container_2d = QWidget(); c2d_lay = QVBoxLayout(self.container_2d); c2d_lay.setContentsMargins(30, 0, 30, 0); c2d_lay.setSpacing(2)
        row_x = QHBoxLayout(); self.lbl_sl_x = QLabel("X:"); self.lbl_sl_x.setFixedWidth(25); self.lbl_sl_x.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 10px;"); self.slider_x = QSlider(Qt.Orientation.Horizontal); self.slider_x.setStyleSheet(STYLE_SLIDER_OVERLAY); self.slider_x.setRange(0, 1000); self.slider_x.setFixedHeight(22); self.slider_x.valueChanged.connect(self._on_slider_2d_moved); row_x.addWidget(self.lbl_sl_x); row_x.addWidget(self.slider_x); c2d_lay.addLayout(row_x)
        row_y = QHBoxLayout(); self.lbl_sl_y = QLabel("Y:"); self.lbl_sl_y.setFixedWidth(25); self.lbl_sl_y.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 10px;"); self.slider_y = QSlider(Qt.Orientation.Horizontal); self.slider_y.setStyleSheet(STYLE_SLIDER_OVERLAY); self.slider_y.setRange(0, 1000); self.slider_y.setFixedHeight(22); self.slider_y.valueChanged.connect(self._on_slider_2d_moved); row_y.addWidget(self.lbl_sl_y); row_y.addWidget(self.slider_y); c2d_lay.addLayout(row_y)
        map_vbox.addWidget(self.container_2d); self.container_1d.setVisible(False); self.container_2d.setVisible(False)
        center_layout.addLayout(map_vbox, 4); self.view_3d = Kinematic3DWidget(); center_layout.addWidget(self.view_3d, 3)
        bar_grid = QGridLayout(); self.motor_bars = []
        names = ["M1 (Y1)", "M2 (Z1)", "M3 (X1)", "M4 (Z2)", "M5 (Y2)", "M6 (Z3)"]
        for i, name in enumerate(names):
            bar = MotorStressBar(name); self.motor_bars.append(bar); bar_grid.addWidget(bar, i//2, i%2)
        center_layout.addLayout(bar_grid); self.splitter.addWidget(center_widget)
        right_panel = QFrame(); right_panel.setMinimumWidth(300); right_layout = QVBoxLayout(right_panel)
        fixed_grp = QGroupBox("FIXED PARAMS FOR MAP"); fixed_grp.setStyleSheet(STYLE_GROUP); fixed_lay = QGridLayout(fixed_grp); self.fixed_inputs = {}
        for i, name in enumerate(["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]):
            lbl_name = QLabel(name); lbl_name.setStyleSheet("color: #e2e8f0; font-weight: bold;"); fixed_lay.addWidget(lbl_name, i//2, (i%2)*2)
            spin = QDoubleSpinBox(); spin.setRange(-500, 500); spin.setDecimals(2); spin.setStyleSheet(STYLE_INPUT); spin.valueChanged.connect(self.update_analysis); fixed_lay.addWidget(spin, i//2, (i%2)*2+1); self.fixed_inputs[name] = spin
        self.btn_sync = QPushButton("SYNC FROM CURRENT POSE"); self.btn_sync.setStyleSheet(STYLE_BTN_SECONDARY); self.btn_sync.clicked.connect(self._on_sync_params); fixed_lay.addWidget(self.btn_sync, 3, 0, 1, 4); right_layout.addWidget(fixed_grp)
        setting_grp = QGroupBox("ANALYSIS SETTINGS"); setting_grp.setStyleSheet(STYLE_GROUP); setting_lay = QGridLayout(setting_grp)
        setting_lay.addWidget(QLabel("Map Res:"), 0, 0); self.spin_res = QSpinBox(); self.spin_res.setRange(20, 300); self.spin_res.setValue(60); self.spin_res.setStyleSheet(STYLE_INPUT); setting_lay.addWidget(self.spin_res, 0, 1)
        setting_lay.addWidget(QLabel("Search Step:"), 1, 0); self.spin_prec = QDoubleSpinBox(); self.spin_prec.setRange(0.01, 1.0); self.spin_prec.setValue(0.1); self.spin_prec.setStyleSheet(STYLE_INPUT); setting_lay.addWidget(self.spin_prec, 1, 1); right_layout.addWidget(setting_grp)
        self.btn_analyze = QPushButton("RE-CALCULATE MAP"); self.btn_analyze.setStyleSheet(STYLE_BTN_PRIMARY); self.btn_analyze.clicked.connect(self.update_analysis); right_layout.addWidget(self.btn_analyze)
        self.btn_autofit = QPushButton("AUTO-FIT LARGEST RECT"); self.btn_autofit.setStyleSheet(STYLE_BTN_SECONDARY); self.btn_autofit.clicked.connect(self._on_autofit); right_layout.addWidget(self.btn_autofit)
        right_layout.addStretch(); self.sim_log = QTextEdit(); self.sim_log.setReadOnly(True); self.sim_log.setStyleSheet("background: #080b14; border: 1px solid #1e293b; color: #4a5a7a; font-family: 'JetBrains Mono'; font-size: 10px;"); self.sim_log.setFixedHeight(120); right_layout.addWidget(self.sim_log); self.splitter.addWidget(right_panel); self.splitter.setSizes([400, 800, 320]); main_layout.addWidget(self.splitter)

    @pyqtSlot()
    def _on_sync_params(self):
        spins = self.acs_panel._dof_spins
        for i, name in enumerate(["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]):
            self.fixed_inputs[name].setValue(spins[i].value())
        self.update_analysis()

    def _on_slider_2d_moved(self, _):
        mode = self.combo_plane.currentText()
        if "XY" in mode: axes = ("Tx", "Ty"); lims = ((-15, 15), (-15, 15))
        elif "XZ" in mode: axes = ("Tx", "Tz"); lims = ((-15, 15), (-15, 15))
        elif "YZ" in mode: axes = ("Ty", "Tz"); lims = ((-15, 15), (-15, 15))
        elif "RxRy" in mode: axes = ("Rx", "Ry"); lims = ((-50, 50), (-50, 50))
        elif "RxRz" in mode: axes = ("Rx", "Rz"); lims = ((-50, 50), (-50, 50))
        elif "RyRz" in mode: axes = ("Ry", "Rz"); lims = ((-50, 50), (-50, 50))
        elif "TxRx" in mode: axes = ("Tx", "Rx"); lims = ((-15, 15), (-50, 50))
        elif "TyRy" in mode: axes = ("Ty", "Ry"); lims = ((-15, 15), (-50, 50))
        elif "TzRz" in mode: axes = ("Tz", "Rz"); lims = ((-15, 15), (-50, 50))
        else: return

        v_x = lims[0][0] + (lims[0][1] - lims[0][0]) * (self.slider_x.value() / 1000.0)
        v_y = lims[1][0] + (lims[1][1] - lims[1][0]) * (self.slider_y.value() / 1000.0)

        for ax, val in zip(axes, [v_x, v_y]):
            self.fixed_inputs[ax].blockSignals(True); self.fixed_inputs[ax].setValue(val); self.fixed_inputs[ax].blockSignals(False)
        
        self._update_motor_stress_from_fixed()
        self.map_widget.current_pos = (v_x, v_y); self.map_widget.update()

    def _on_slider_moved_internal(self, val):
        mode = self.combo_plane.currentText(); axis_name = mode.split(" ")[0]; min_v, max_v = self.current_1d_range
        mapped_val = min_v + (max_v - min_v) * (val / 1000.0)
        self.fixed_inputs[axis_name].blockSignals(True); self.fixed_inputs[axis_name].setValue(mapped_val); self.fixed_inputs[axis_name].blockSignals(False)
        self._update_motor_stress_from_fixed()
        self.map_widget.set_data(None, self.current_1d_range, (0, 1), mapped_val, 0.5, mode, labels=(axis_name, "-"))

    def _update_motor_stress_from_fixed(self):
        f = {k: v.value() for k, v in self.fixed_inputs.items()}
        cal_pos, ball_pos, ok, violations = self.calc.calculate([f['Tx'], f['Ty'], f['Tz']], [f['Rx'], f['Ry'], f['Rz']])
        if ball_pos is not None: self.view_3d.set_geometry(self.calc.stage_setup, ball_pos)
        if cal_pos is not None:
            for i in range(6): self.motor_bars[i].set_value(cal_pos[i], self.calc.minus_limits[i], self.calc.plus_limits[i])

    @pyqtSlot()
    def _on_autofit(self):
        if self.map_widget.matrix is not None:
            plane_mode = self.combo_plane.currentText(); lims = (-50, 50) if "RxRy" in plane_mode else (-15, 15)
            xs = np.linspace(lims[0], lims[1], self.map_widget.matrix.shape[1]); ys = np.linspace(lims[0], lims[1], self.map_widget.matrix.shape[0])
            m_x, M_x, m_y, M_y = self.calc.find_largest_rectangle(self.map_widget.matrix, xs, ys); self.map_widget.fit_rect = (m_x, M_x, m_y, M_y); self.map_widget.update()

    @pyqtSlot()
    def update_analysis(self):
        try:
            f = {k: v.value() for k, v in self.fixed_inputs.items()}; t_fixed, r_fixed = [f['Tx'], f['Ty'], f['Tz']], [f['Rx'], f['Ry'], f['Rz']]
            spins = self.acs_panel._dof_spins; t_curr = [spins[0].value(), spins[1].value(), spins[2].value()]; r_curr = [spins[3].value()*1000.0, spins[4].value()*1000.0, spins[5].value()*1000.0]
            mode = self.combo_plane.currentText(); is_1d = "1D Search" in mode; self.container_1d.setVisible(is_1d); self.container_2d.setVisible(not is_1d); res = self.spin_res.value(); step_v = self.spin_prec.value()
            if "XY" in mode:
                matrix = self.calc.get_reachability_matrix('XY', {'tz':f['Tz'], 'rx':f['Rx'], 'ry':f['Ry'], 'rz':f['Rz']}, (-15, 15), (-15, 15), resolution=res)
                self.map_widget.set_data(matrix, (-15, 15), (-15, 15), t_curr[0], t_curr[1], mode, labels=("Tx", "Ty"))
                self.lbl_sl_x.setText("Tx:"); self.lbl_sl_y.setText("Ty:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((t_curr[0]+15)/30*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((t_curr[1]+15)/30*1000)); self.slider_y.blockSignals(False)
            elif "XZ" in mode:
                matrix = self.calc.get_reachability_matrix('XZ', {'ty':f['Ty'], 'rx':f['Rx'], 'ry':f['Ry'], 'rz':f['Rz']}, (-15, 15), (-15, 15), resolution=res)
                self.map_widget.set_data(matrix, (-15, 15), (-15, 15), t_curr[0], t_curr[2], mode, labels=("Tx", "Tz"))
                self.lbl_sl_x.setText("Tx:"); self.lbl_sl_y.setText("Tz:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((t_curr[0]+15)/30*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((t_curr[2]+15)/30*1000)); self.slider_y.blockSignals(False)
            elif "YZ" in mode:
                matrix = self.calc.get_reachability_matrix('YZ', {'tx':f['Tx'], 'rx':f['Rx'], 'ry':f['Ry'], 'rz':f['Rz']}, (-15, 15), (-15, 15), resolution=res)
                self.map_widget.set_data(matrix, (-15, 15), (-15, 15), t_curr[1], t_curr[2], mode, labels=("Ty", "Tz"))
                self.lbl_sl_x.setText("Ty:"); self.lbl_sl_y.setText("Tz:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((t_curr[1]+15)/30*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((t_curr[2]+15)/30*1000)); self.slider_y.blockSignals(False)
            elif "RxRy" in mode:
                matrix = self.calc.get_reachability_matrix('RxRy', {'tx':f['Tx'], 'ty':f['Ty'], 'tz':f['Tz'], 'rz':f['Rz']}, (-50, 50), (-50, 50), resolution=res)
                self.map_widget.set_data(matrix, (-50, 50), (-50, 50), r_curr[0], r_curr[1], mode, labels=("Rx", "Ry"))
                self.lbl_sl_x.setText("Rx:"); self.lbl_sl_y.setText("Ry:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((r_curr[0]+50)/100*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((r_curr[1]+50)/100*1000)); self.slider_y.blockSignals(False)
            elif "RxRz" in mode:
                matrix = self.calc.get_reachability_matrix('RxRz', {'tx':f['Tx'], 'ty':f['Ty'], 'tz':f['Tz'], 'ry':f['Ry']}, (-50, 50), (-50, 50), resolution=res)
                self.map_widget.set_data(matrix, (-50, 50), (-50, 50), r_curr[0], r_curr[2], mode, labels=("Rx", "Rz"))
                self.lbl_sl_x.setText("Rx:"); self.lbl_sl_y.setText("Rz:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((r_curr[0]+50)/100*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((r_curr[2]+50)/100*1000)); self.slider_y.blockSignals(False)
            elif "RyRz" in mode:
                matrix = self.calc.get_reachability_matrix('RyRz', {'tx':f['Tx'], 'ty':f['Ty'], 'tz':f['Tz'], 'rx':f['Rx']}, (-50, 50), (-50, 50), resolution=res)
                self.map_widget.set_data(matrix, (-50, 50), (-50, 50), r_curr[1], r_curr[2], mode, labels=("Ry", "Rz"))
                self.lbl_sl_x.setText("Ry:"); self.lbl_sl_y.setText("Rz:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((r_curr[1]+50)/100*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((r_curr[2]+50)/100*1000)); self.slider_y.blockSignals(False)
            elif "TxRx" in mode:
                matrix = self.calc.get_reachability_matrix('TxRx', {'ty':f['Ty'], 'tz':f['Tz'], 'ry':f['Ry'], 'rz':f['Rz']}, (-15, 15), (-50, 50), resolution=res)
                self.map_widget.set_data(matrix, (-15, 15), (-50, 50), t_curr[0], r_curr[0], mode, labels=("Tx", "Rx"))
                self.lbl_sl_x.setText("Tx:"); self.lbl_sl_y.setText("Rx:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((t_curr[0]+15)/30*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((r_curr[0]+50)/100*1000)); self.slider_y.blockSignals(False)
            elif "TyRy" in mode:
                matrix = self.calc.get_reachability_matrix('TyRy', {'tx':f['Tx'], 'tz':f['Tz'], 'rx':f['Rx'], 'rz':f['Rz']}, (-15, 15), (-50, 50), resolution=res)
                self.map_widget.set_data(matrix, (-15, 15), (-50, 50), t_curr[1], r_curr[1], mode, labels=("Ty", "Ry"))
                self.lbl_sl_x.setText("Ty:"); self.lbl_sl_y.setText("Ry:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((t_curr[1]+15)/30*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((r_curr[1]+50)/100*1000)); self.slider_y.blockSignals(False)
            elif "TzRz" in mode:
                matrix = self.calc.get_reachability_matrix('TzRz', {'tx':f['Tx'], 'ty':f['Ty'], 'rx':f['Rx'], 'ry':f['Ry']}, (-15, 15), (-50, 50), resolution=res)
                self.map_widget.set_data(matrix, (-15, 15), (-50, 50), t_curr[2], r_curr[2], mode, labels=("Tz", "Rz"))
                self.lbl_sl_x.setText("Tz:"); self.lbl_sl_y.setText("Rz:")
                self.slider_x.blockSignals(True); self.slider_x.setValue(int((t_curr[2]+15)/30*1000)); self.slider_x.blockSignals(False)
                self.slider_y.blockSignals(True); self.slider_y.setValue(int((r_curr[2]+50)/100*1000)); self.slider_y.blockSignals(False)
            elif is_1d:
                axis_name = mode.split(" ")[0]; axis_idx = {"Tx":0, "Ty":1, "Tz":2, "Rx":3, "Ry":4, "Rz":5}[axis_name]
                min_v, max_v = self.calc.get_axis_limits(axis_idx, t_fixed, r_fixed, step=step_v); self.current_1d_range = (min_v, max_v)
                curr_val = f[axis_name]
                if max_v != min_v:
                    ratio = (curr_val - min_v) / (max_v - min_v); self.slider_1d.blockSignals(True); self.slider_1d.setValue(int(np.clip(ratio, 0, 1) * 1000)); self.slider_1d.blockSignals(False)
                self.map_widget.set_data(None, self.current_1d_range, (0, 1), curr_val, 0.5, mode, labels=(axis_name, "-"))
            self._update_motor_stress_from_fixed()
        except Exception as e: log.error(f"Analysis Error: {e}")
