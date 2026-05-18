"""
ui/widgets/acs_settings_panel.py
ACS 6-Axis Stage 운동학(Kinematic) 설정 전용 패널.
"""

from __future__ import annotations
from typing import Optional
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, 
    QPushButton, QFrame, QDoubleSpinBox, QComboBox, QCheckBox, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from core.config import get_config
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_TEXT, C_TEXT_DIM,
    Fonts, BTN_SMALL, SPIN_STYLE, EDIT_STYLE, CHECKBOX_STYLE, lbl
)
from ui.widgets.collapsible_section import CollapsibleSection
from core.motor.kinematic_calc import KinematicCalc

class AcsSettingsCard(QFrame):
    log_message = pyqtSignal(str)
    settings_applied = pyqtSignal()

    def __init__(self, calc: Optional[KinematicCalc] = None, parent=None):
        super().__init__(parent)
        self._calc = calc or KinematicCalc()
        self._session_hub = None
        self._cfg = get_config()
        self.setObjectName("motionCard")
        
        # Default copies for reset
        self._default_stage_setup = self._calc.stage_setup.copy()
        self._default_encoder_pos = self._calc.encoder_pos.copy()
        self._default_plus_limits = self._calc.plus_limits.copy()
        self._default_minus_limits = self._calc.minus_limits.copy()
        self._default_direction = self._calc.direction.copy()
        self._default_mapping = self._calc._mapping.copy()

        self.setStyleSheet(f"""
            QFrame#motionCard {{
                background: #0f1729;
                border: 1px solid #11345f;
                border-radius: 6px;
            }}
            QFrame#motionSection {{
                background: #0b1222;
                border: 1px solid #13223d;
                border-radius: 4px;
            }}
        """)
        
        self._stage_setup_spins: list[QDoubleSpinBox] = []
        self._encoder_pos_spins: list[QDoubleSpinBox] = []
        self._limit_plus_spins: list[QDoubleSpinBox] = []
        self._limit_minus_spins: list[QDoubleSpinBox] = []
        self._dir_combos: list[QComboBox] = []
        self._map_combos: list[QComboBox] = []
        self._pivot_spins: list[QDoubleSpinBox] = []
        self.spin_beam_z = QDoubleSpinBox()
        
        self._build_ui()
        self.load_settings(apply_after_load=False)

    def _section_box(self, title: str, accent: str) -> CollapsibleSection:
        sec = CollapsibleSection(title, accent=accent)
        sec.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        return sec

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        title = QLabel("▾  ACS KINEMATIC SETTINGS")
        title.setStyleSheet(f"color: {C_WARN}; font-family: '{Fonts.MONO}'; font-size: 18px; font-weight: bold; letter-spacing: 1px;")
        lay.addWidget(title)

        # 0) Global Parameters (Pivot & Beam)
        sec_global = self._section_box("GLOBAL PARAMETERS", C_ACCENT)
        glob_l = sec_global.content_layout()
        grid_glob = QGridLayout()
        grid_glob.setSpacing(4)
        
        # Pivot X, Y, Z
        grid_glob.addWidget(QLabel("PIVOT (X,Y,Z)"), 0, 0)
        for i in range(3):
            spin = QDoubleSpinBox()
            spin.setRange(-99999.0, 99999.0); spin.setDecimals(4)
            spin.setStyleSheet(SPIN_STYLE); spin.setFixedHeight(24)
            self._pivot_spins.append(spin)
            grid_glob.addWidget(spin, 0, i + 1)
        
        # Beam Z
        grid_glob.addWidget(QLabel("BEAM Z (deg)"), 1, 0)
        self.spin_beam_z.setRange(-360.0, 360.0); self.spin_beam_z.setDecimals(4)
        self.spin_beam_z.setStyleSheet(SPIN_STYLE); self.spin_beam_z.setFixedHeight(24)
        grid_glob.addWidget(self.spin_beam_z, 1, 1)
        
        glob_l.addLayout(grid_glob)
        lay.addWidget(sec_global)

        # 1) Stage Setup
        sec_setup = self._section_box("STAGE SETUP (S1-S3)", C_ACCENT)
        setup_l = sec_setup.content_layout()
        grid_setup = QGridLayout()
        grid_setup.setSpacing(4)
        for c, text in enumerate(["", "X", "Y", "Z"]):
            h = QLabel(text)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            grid_setup.addWidget(h, 0, c)
        
        for r in range(3):
            s_lbl = QLabel(f"S{r+1}")
            s_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            grid_setup.addWidget(s_lbl, r + 1, 0)
            for c in range(3):
                spin = QDoubleSpinBox()
                spin.setRange(-99999.0, 99999.0); spin.setDecimals(4)
                spin.setStyleSheet(SPIN_STYLE)
                spin.setFixedHeight(24)
                self._stage_setup_spins.append(spin)
                grid_setup.addWidget(spin, r + 1, c + 1)
        setup_l.addLayout(grid_setup)
        lay.addWidget(sec_setup)

        # 2) Encoder Pos
        sec_enc = self._section_box("ENCODER POS", C_ACCENT)
        enc_l = sec_enc.content_layout()
        grid_enc = QGridLayout()
        grid_enc.setSpacing(4)
        for r in range(3):
            s_lbl = QLabel(f"S{r+1}")
            s_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            grid_enc.addWidget(s_lbl, r, 0)
            for c in range(3):
                spin = QDoubleSpinBox()
                spin.setRange(-99999.0, 99999.0); spin.setDecimals(4)
                spin.setStyleSheet(SPIN_STYLE)
                spin.setFixedHeight(24)
                self._encoder_pos_spins.append(spin)
                grid_enc.addWidget(spin, r, c + 1)
        enc_l.addLayout(grid_enc)
        lay.addWidget(sec_enc)

        # 3) Limits
        sec_lim = self._section_box("AXIS LIMITS", C_WARN)
        sec_lim.set_collapsed(True)
        lim_l = sec_lim.content_layout()
        grid_lim = QGridLayout()
        grid_lim.setSpacing(4)
        axes = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
        for i, axis in enumerate(axes):
            ax_lbl = QLabel(axis)
            ax_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            sp = QDoubleSpinBox(); sm = QDoubleSpinBox()
            for s in (sp, sm):
                s.setRange(-99999.0, 99999.0); s.setDecimals(4)
                s.setStyleSheet(SPIN_STYLE); s.setFixedHeight(24)
            self._limit_plus_spins.append(sp)
            self._limit_minus_spins.append(sm)
            grid_lim.addWidget(ax_lbl, i, 0)
            grid_lim.addWidget(sp, i, 1)
            grid_lim.addWidget(sm, i, 2)
        lim_l.addLayout(grid_lim)
        lay.addWidget(sec_lim)

        # 4) Direction & Mapping
        sec_adv = self._section_box("DIRECTION & MAPPING", C_ACCENT)
        sec_adv.set_collapsed(True)
        adv_l = sec_adv.content_layout()
        grid_adv = QGridLayout()
        grid_adv.setSpacing(4)
        
        # Direction Headers
        grid_adv.addWidget(QLabel("DIR (S1-S3)"), 0, 0)
        for c, text in enumerate(["X", "Y", "Z"]):
            h = QLabel(text)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            grid_adv.addWidget(h, 0, c + 1)

        for r in range(3):
            s_lbl = QLabel(f"S{r+1}")
            s_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            grid_adv.addWidget(s_lbl, r + 1, 0)
            for c in range(3):
                cb = QComboBox()
                cb.addItems(["Forward (+1)", "Reverse (-1)"])
                cb.setStyleSheet(EDIT_STYLE)
                cb.setFixedHeight(24)
                self._dir_combos.append(cb)
                grid_adv.addWidget(cb, r + 1, c + 1)

        # Mapping Headers (Separated by small spacer)
        spacer = QFrame(); spacer.setFixedHeight(10); grid_adv.addWidget(spacer, 4, 0, 1, 4)
        grid_adv.addWidget(QLabel("MAP (S1-S3)"), 5, 0)
        for c, text in enumerate(["X", "Y", "Z"]):
            h = QLabel(text)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet(lbl(C_TEXT_DIM, mono=True))
            grid_adv.addWidget(h, 5, c + 1)

        for r in range(3):
            s_lbl = QLabel(f"S{r+1}")
            s_lbl.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            grid_adv.addWidget(s_lbl, r + 6, 0)
            for c in range(3):
                cb = QComboBox()
                cb.addItems(["0.0 (OFF)", "1.0 (ON)"])
                cb.setStyleSheet(EDIT_STYLE)
                cb.setFixedHeight(24)
                self._map_combos.append(cb)
                grid_adv.addWidget(cb, r + 6, c + 1)

        adv_l.addLayout(grid_adv)
        lay.addWidget(sec_adv)

        # Action Buttons
        btn_row = QHBoxLayout()
        self.btn_apply = QPushButton("APPLY")
        self.btn_save = QPushButton("SAVE")
        self.btn_load = QPushButton("LOAD")
        self.btn_reset = QPushButton("RESET")
        for b in (self.btn_apply, self.btn_save, self.btn_load, self.btn_reset):
            b.setStyleSheet(BTN_SMALL)
            b.setFixedHeight(28)
            btn_row.addWidget(b)
        
        self.btn_apply.clicked.connect(self.apply_to_calc)
        self.btn_save.clicked.connect(self.save_settings)
        self.btn_load.clicked.connect(self.load_settings)
        self.btn_reset.clicked.connect(self.reset_to_default)
        
        lay.addLayout(btn_row)
        lay.addStretch()

    def apply_to_calc(self):
        try:
            # Global
            for i, spin in enumerate(self._pivot_spins):
                self._calc.pivot[i] = float(spin.value())
            self._calc.beam_z_deg = float(self.spin_beam_z.value())
            
            # Stage Setup
            for i, spin in enumerate(self._stage_setup_spins):
                self._calc.stage_setup.reshape(-1)[i] = float(spin.value())
            # Encoder Pos
            for i, spin in enumerate(self._encoder_pos_spins):
                self._calc.encoder_pos.reshape(-1)[i] = float(spin.value())
            # Limits
            for i in range(6):
                self._calc.plus_limits[i] = float(self._limit_plus_spins[i].value())
                self._calc.minus_limits[i] = float(self._limit_minus_spins[i].value())
            
            # Direction
            for i, cb in enumerate(self._dir_combos):
                # "Forward (+1)" -> 1.0, "Reverse (-1)" -> -1.0
                val = 1.0 if cb.currentIndex() == 0 else -1.0
                self._calc.direction.reshape(-1)[i] = val
            
            # Mapping
            for i, cb in enumerate(self._map_combos):
                val = 0.0 if cb.currentIndex() == 0 else 1.0
                self._calc._mapping[i] = val
            
            # Sync to SessionHub/MotionHub if available
            if self._session_hub:
                self._session_hub.motion().update_kinematics(self._calc)
                self.log_message.emit("ACS Kinematic settings synced to MotionHub.")

            self.settings_applied.emit()
            self.log_message.emit("ACS Kinematic settings applied to local engine.")
        except Exception as e:
            self.log_message.emit(f"Apply failed: {e}")

    def bind_session_hub(self, hub):
        self._session_hub = hub
        self.log_message.emit("ACS Settings bound to SessionHub.")

    def save_settings(self):
        self.apply_to_calc()
        c = self._cfg
        c.set("devices.acs.stage_setup",  [sp.value() for sp in self._stage_setup_spins])
        c.set("devices.acs.encoder_pos",  [sp.value() for sp in self._encoder_pos_spins])
        c.set("devices.acs.plus_limits",  [sp.value() for sp in self._limit_plus_spins])
        c.set("devices.acs.minus_limits", [sp.value() for sp in self._limit_minus_spins])
        c.set("devices.acs.direction",    [cb.currentIndex() for cb in self._dir_combos])
        c.set("devices.acs.mapping",      [cb.currentIndex() for cb in self._map_combos])
        c.set("devices.acs.pivot",        [sp.value() for sp in self._pivot_spins])
        c.set("devices.acs.beam_z",       self.spin_beam_z.value())
        c.save()
        self.log_message.emit("ACS Kinematic settings saved.")

    def load_settings(self, apply_after_load=True):
        c = self._cfg

        def _get(key, default):
            return c.get(f"devices.acs.{key}", default)

        setup = _get("stage_setup", list(self._default_stage_setup.reshape(-1)))
        enc = _get("encoder_pos", list(self._default_encoder_pos.reshape(-1)))
        plus = _get("plus_limits", list(self._default_plus_limits))
        minus = _get("minus_limits", list(self._default_minus_limits))
        piv = _get("pivot", list(self._calc.pivot))
        bz = _get("beam_z", self._calc.beam_z_deg)

        for spin, val in zip(self._pivot_spins, piv): spin.setValue(float(val))
        self.spin_beam_z.setValue(float(bz))

        for spins, vals in [(self._stage_setup_spins, setup), (self._encoder_pos_spins, enc)]:
            for spin, val in zip(spins, vals):
                spin.setValue(float(val))
        for spin, val in zip(self._limit_plus_spins, plus): spin.setValue(float(val))
        for spin, val in zip(self._limit_minus_spins, minus): spin.setValue(float(val))

        dirs = _get("direction", [0 if d > 0 else 1 for d in self._default_direction.reshape(-1)])
        maps = _get("mapping", [0 if m < 0.5 else 1 for m in self._default_mapping])

        for cb, val in zip(self._dir_combos, dirs): cb.setCurrentIndex(int(val))
        for cb, val in zip(self._map_combos, maps): cb.setCurrentIndex(int(val))

        if apply_after_load: self.apply_to_calc()

    def reset_to_default(self):
        for spin, val in zip(self._pivot_spins, list(DEFAULT_PIVOT)): spin.setValue(float(val))
        self.spin_beam_z.setValue(float(DEFAULT_BEAM_Z_PATH_DEG))

        setup = list(self._default_stage_setup.reshape(-1))
        enc = list(self._default_encoder_pos.reshape(-1))
        plus = list(self._default_plus_limits)
        minus = list(self._default_minus_limits)

        for spins, vals in [(self._stage_setup_spins, setup), (self._encoder_pos_spins, enc)]:
            for spin, val in zip(spins, vals): spin.setValue(float(val))
        for spin, val in zip(self._limit_plus_spins, plus): spin.setValue(float(val))
        for spin, val in zip(self._limit_minus_spins, minus): spin.setValue(float(val))
        
        # Reset Direction & Mapping
        for i, val in enumerate(self._default_direction.reshape(-1)):
            idx = 0 if val > 0 else 1
            self._dir_combos[i].setCurrentIndex(idx)
        for i, val in enumerate(self._default_mapping):
            idx = 0 if val < 0.5 else 1
            self._map_combos[i].setCurrentIndex(idx)

        self.apply_to_calc()
