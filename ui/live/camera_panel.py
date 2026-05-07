"""
ui/live/camera_panel.py
Capability 기반 동적 카메라 제어 패널.

카메라가 연결되면 CameraCapabilities를 읽어
지원하지 않는 컨트롤 그룹을 자동으로 숨긴다.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QRadioButton,
    QButtonGroup, QListWidget, QSizePolicy
)
from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, QSettings
from core.async_worker import TempPollerThread

from core.camera.base import BaseCamera, CameraCapabilities
from core.image_processor import (
    ImageProcessor,
    BIN_BINARY, BIN_BINARY_INV, BIN_TOZERO, BIN_TOZERO_INV,
    DisplayStretch, TemporalMode, CentroidMode,
)

class _CamCommandWorker(QObject):
    """임의의 카메라 SDK 호출을 백그라운드에서 실행 — 메인 스레드 블로킹 방지."""
    success = pyqtSignal(object)
    error   = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.success.emit(self._fn())
        except Exception as e:
            self.error.emit(str(e))


from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    Fonts, Sizes,
    C_ACCENT, C_DANGER, C_TEXT, C_TEXT_DIM,
    BTN_PRIMARY, BTN_SMALL,
    SPIN_STYLE, COMBO_STYLE, CHECKBOX_STYLE,
    grp_style, lbl,
)

_LBL_STYLE   = lbl()
_SPIN_STYLE  = SPIN_STYLE
_BTN_STYLE   = BTN_SMALL
_CHECK_STYLE = CHECKBOX_STYLE


class CameraControlPanel(QWidget):
    """
    카메라 연결/해제 + 파라미터 제어 패널.

    외부에서 attach_camera(cam) 로 카메라를 연결하고
    detach_camera() 로 해제한다.
    ImageProcessor 인스턴스를 공유해 처리 파라미터를 실시간 반영한다.
    """

    # ── 시그널 ────────────────────────────────────────────────────────
    camera_scan_requested   = pyqtSignal()          # SCAN 버튼
    camera_connect_requested= pyqtSignal(int)       # CONNECT (index)
    camera_disconnect_requested = pyqtSignal()
    camera_start_requested  = pyqtSignal()
    camera_stop_requested   = pyqtSignal()
    snap_requested          = pyqtSignal()          # 1장 촬영
    bg_capture_requested    = pyqtSignal()
    log_message             = pyqtSignal(str)
    exposure_applied        = pyqtSignal(float)

    def __init__(self, processor: ImageProcessor, parent=None):
        super().__init__(parent)
        self._proc = processor
        self._cam: Optional[BaseCamera] = None
        self._caps: Optional[CameraCapabilities] = None
        self._temp_thread: Optional[TempPollerThread] = None
        self._cmd_thread:  Optional[QThread] = None
        self._cmd_worker:  Optional[_CamCommandWorker] = None
        self._build_ui()
        self._set_connected(False)

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── 카메라 선택 그룹 ──────────────────────────────────────────
        grp_dev = CollapsibleSection("CAMERA DEVICE")
        gd = grp_dev.content_layout()

        # 카메라 종류 선택
        type_row = QHBoxLayout()
        self.combo_cam_type = QComboBox()
        self.combo_cam_type.addItems(["HIKVISION", "Picam", "SIMULATED"])
        self.combo_cam_type.setStyleSheet("""
            QComboBox { background: #080e1e; border: 1px solid #0f3460;
                color: #c0d0ff; border-radius: 3px;
                font-family: 'Courier New'; font-size: 11px; padding: 2px 6px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #0f1729; color: #c0d0ff; }
        """)
        type_row.addWidget(QLabel("Type:"))
        type_row.addWidget(self.combo_cam_type)
        gd.addLayout(type_row)

        # 디바이스 목록
        self.camera_list = QListWidget()
        self.camera_list.setFixedHeight(64)
        self.camera_list.setStyleSheet("""
            QListWidget { background: #080e1e; border: 1px solid #0f3460;
                color: #8090a8; font-family: 'Courier New'; font-size: 11px; }
            QListWidget::item:selected { background: #0f3460; color: #e94560; }
        """)
        gd.addWidget(self.camera_list)

        row1 = QHBoxLayout()
        self.btn_scan = QPushButton("SCAN")
        self.btn_connect = QPushButton("CONNECT")
        self.btn_disconnect = QPushButton("DISCONNECT")
        for btn in (self.btn_scan, self.btn_connect, self.btn_disconnect):
            btn.setStyleSheet(_BTN_STYLE)
        row1.addWidget(self.btn_scan)
        row1.addWidget(self.btn_connect)
        row1.addWidget(self.btn_disconnect)
        gd.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_start = QPushButton("▶ START")
        self.btn_stop  = QPushButton("■ STOP")
        self.btn_start.setStyleSheet(_BTN_STYLE)
        self.btn_stop.setStyleSheet(_BTN_STYLE.replace("#4ecdc4", "#e94560"))
        row2.addWidget(self.btn_start)
        row2.addWidget(self.btn_stop)
        gd.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_snap = QPushButton("📷 SNAP")
        self.btn_snap.setStyleSheet(_BTN_STYLE.replace("#4ecdc4", "#ffe66d"))
        self.btn_snap.setToolTip("단일 프레임 촬영 (라이브 정지 상태에서 사용)")
        row3.addWidget(self.btn_snap)

        self.check_gil_block = QCheckBox("Simulate GIL Block")
        self.check_gil_block.setStyleSheet(_CHECK_STYLE)
        self.check_gil_block.setToolTip("체크 시 UI 멈춤(GIL 독점) 현상 재현")
        self.check_gil_block.setVisible(False)
        row3.addWidget(self.check_gil_block)

        gd.addLayout(row3)
        layout.addWidget(grp_dev) 

        # ── 노출 그룹 ─────────────────────────────────────────────────
        grp_exp = CollapsibleSection("EXPOSURE")
        ge = QHBoxLayout()
        grp_exp.add_layout(ge)
        lbl_exp = QLabel("ms:")
        lbl_exp.setStyleSheet(_LBL_STYLE)
        self.spin_exposure = QDoubleSpinBox()
        self.spin_exposure.setRange(0.01, 1_000_000.0)
        self.spin_exposure.setDecimals(2)
        self.spin_exposure.setValue(20.0)
        self.spin_exposure.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_exp = QPushButton("APPLY")
        self.btn_apply_exp.setStyleSheet(_BTN_STYLE)
        ge.addWidget(lbl_exp)
        ge.addWidget(self.spin_exposure, 1)
        ge.addWidget(self.btn_apply_exp)
        layout.addWidget(grp_exp)

        # ── FPS 그룹 (HIKVISION 전용) ─────────────────────────────────
        self.grp_fps = CollapsibleSection("FRAMERATE")
        gf = QHBoxLayout()
        self.grp_fps.add_layout(gf)
        self.check_fps_lock = QCheckBox("Lock")
        self.check_fps_lock.setStyleSheet(_CHECK_STYLE)
        self.spin_fps = QDoubleSpinBox()
        self.spin_fps.setRange(0.1, 1000.0)
        self.spin_fps.setValue(30.0)
        self.spin_fps.setSuffix(" fps")
        self.spin_fps.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_fps = QPushButton("APPLY")
        self.btn_apply_fps.setStyleSheet(_BTN_STYLE)
        gf.addWidget(self.check_fps_lock)
        gf.addWidget(self.spin_fps, 1)
        gf.addWidget(self.btn_apply_fps)
        layout.addWidget(self.grp_fps)

        # ── 평균화 그룹 ───────────────────────────────────────────────
        grp_avg = CollapsibleSection("AVERAGING")
        ga = grp_avg.content_layout()
        avg_row = QHBoxLayout()
        lbl_avg = QLabel("N frames:")
        lbl_avg.setStyleSheet(_LBL_STYLE)
        self.spin_avg = QSpinBox()
        self.spin_avg.setRange(1, 500)
        self.spin_avg.setValue(5)
        self.spin_avg.setStyleSheet(_SPIN_STYLE)
        avg_row.addWidget(lbl_avg)
        avg_row.addWidget(self.spin_avg)
        avg_row.addStretch()
        ga.addLayout(avg_row)
        # Temporal mode
        mode_row = QHBoxLayout()
        lbl_mode = QLabel("Mode:")
        lbl_mode.setStyleSheet(_LBL_STYLE)
        self.combo_temporal = QComboBox()
        self.combo_temporal.addItems([
            "Average", "Max Proj", "Min Proj", "Std Map", "Accumulate", "Live (Single)"
        ])
        self.combo_temporal.setStyleSheet("""
            QComboBox { background: #080e1e; border: 1px solid #0f3460;
                color: #c0d0ff; border-radius: 3px;
                font-family: 'Courier New'; font-size: 11px; padding: 2px 4px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #0f1729; color: #c0d0ff; }
        """)
        self.btn_reset_accum = QPushButton("RESET")
        self.btn_reset_accum.setStyleSheet(_BTN_STYLE)
        self.btn_reset_accum.setToolTip("Accumulate 버퍼 초기화")
        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.combo_temporal, 1)
        mode_row.addWidget(self.btn_reset_accum)
        ga.addLayout(mode_row)
        layout.addWidget(grp_avg)

        # ── 이진화 / Centroid 그룹 (HIKVISION 전용) ──────────────────
        self.grp_proc = CollapsibleSection("PROCESSING")
        gp = self.grp_proc.content_layout()

        self.check_bin = QCheckBox("Binarize (centroid용)")
        self.check_bin.setChecked(True)
        self.check_bin.setStyleSheet(_CHECK_STYLE)
        gp.addWidget(self.check_bin)

        lbl_thresh_row = QHBoxLayout()
        lbl_thresh_row.setContentsMargins(0, 0, 0, 0)
        lbl_thresh_row.setSpacing(6)
        lbl_thresh_row.addWidget(QLabel("Threshold:"))
        lbl_thresh_row.itemAt(0).widget().setStyleSheet(_LBL_STYLE)
        self.spin_bin_thresh = QSpinBox()
        self.spin_bin_thresh.setRange(0, 65535)
        self.spin_bin_thresh.setValue(1000)
        self.spin_bin_thresh.setSingleStep(100)
        self.spin_bin_thresh.setFixedWidth(80)
        self.spin_bin_thresh.setStyleSheet("""
            QSpinBox { background: #0f1e38; color: #c0d0ff; border: 1px solid #1a3460;
                border-radius: 3px; padding: 1px 4px;
                font-family: 'Consolas'; font-size: 11px; }
            QSpinBox::up-button, QSpinBox::down-button { width: 14px; background: #1a3060; }
        """)
        lbl_thresh_row.addWidget(self.spin_bin_thresh)
        lbl_thresh_row.addStretch()
        thresh_row_w = QWidget()
        thresh_row_w.setLayout(lbl_thresh_row)
        gp.addWidget(thresh_row_w)

        self.check_show_bin = QCheckBox("Show binary on screen")
        self.check_show_bin.setStyleSheet(_CHECK_STYLE)
        gp.addWidget(self.check_show_bin)

        # 이진화 모드 라디오
        thresh_grp = CollapsibleSection("Threshold Mode", accent=C_ACCENT)
        tg = thresh_grp.content_layout()
        self._thresh_btn_grp = QButtonGroup()
        thresh_items = [
            ("Greater  (밝은 영역)", BIN_BINARY),
            ("Less     (어두운 영역)", BIN_BINARY_INV),
            ("Inner    (임계 이하→0)", BIN_TOZERO),
            ("Outer    (임계 이상→0)", BIN_TOZERO_INV),
        ]
        self._thresh_radios = {}
        for label, mode in thresh_items:
            rb = QRadioButton(label)
            rb.setStyleSheet(_CHECK_STYLE)
            if mode == BIN_BINARY:
                rb.setChecked(True)
            self._thresh_btn_grp.addButton(rb, mode)
            self._thresh_radios[mode] = rb
            tg.addWidget(rb)
        gp.addWidget(thresh_grp)

        # Centroid 계산 모드
        centroid_mode_grp = CollapsibleSection("Centroid Mode", accent=C_ACCENT)
        cmg = centroid_mode_grp.content_layout()
        self._centroid_mode_grp = QButtonGroup()
        self.rb_centroid_binary   = QRadioButton("Binary")
        self.rb_centroid_weighted = QRadioButton("Weighted")
        self.rb_centroid_binary.setChecked(True)
        for rb in (self.rb_centroid_binary, self.rb_centroid_weighted):
            rb.setStyleSheet(_CHECK_STYLE)
            cmg.addWidget(rb)
        self._centroid_mode_grp.addButton(self.rb_centroid_binary,   0)
        self._centroid_mode_grp.addButton(self.rb_centroid_weighted, 1)
        gp.addWidget(centroid_mode_grp)
        layout.addWidget(self.grp_proc)

        # ── 로그 스케일 그룹 (HIKVISION 전용) ────────────────────────
        self.grp_log = CollapsibleSection("LOG SCALE")
        gl = self.grp_log.content_layout()
        self.check_log = QCheckBox("Log Scale 활성화")
        self.check_log.setStyleSheet(_CHECK_STYLE)
        log_row = QHBoxLayout()
        lbl_lv = QLabel("Level:")
        lbl_lv.setStyleSheet(_LBL_STYLE)
        self.spin_log = QDoubleSpinBox()
        self.spin_log.setRange(0.1, 3.0)
        self.spin_log.setSingleStep(0.1)
        self.spin_log.setValue(1.0)
        self.spin_log.setDecimals(1)
        self.spin_log.setStyleSheet(_SPIN_STYLE)
        log_row.addWidget(lbl_lv)
        log_row.addWidget(self.spin_log)
        gl.addWidget(self.check_log)
        gl.addLayout(log_row)
        layout.addWidget(self.grp_log)

        # ── 배경 차분 그룹 (HIKVISION 전용) ──────────────────────────
        self.grp_bg = CollapsibleSection("BACKGROUND")
        gb = self.grp_bg.content_layout()
        bg_row = QHBoxLayout()
        self.btn_cap_bg = QPushButton("CAPTURE BG")
        self.btn_cap_bg.setStyleSheet(_BTN_STYLE)
        self.btn_bg_toggle = QPushButton("BG SUB OFF")
        self.btn_bg_toggle.setCheckable(True)
        self.btn_bg_toggle.setStyleSheet(_BTN_STYLE)
        bg_row.addWidget(self.btn_cap_bg)
        bg_row.addWidget(self.btn_bg_toggle)
        gb.addLayout(bg_row)
        # Temporal diff
        self.check_tdiff = QCheckBox("Frame Diff (시간 차분)")
        self.check_tdiff.setStyleSheet(_CHECK_STYLE)
        gb.addWidget(self.check_tdiff)
        layout.addWidget(self.grp_bg)

        # ── Dark / Flat 그룹 (소프트웨어, 모든 카메라) ───────────────
        self.grp_dark = CollapsibleSection("DARK / FLAT")
        gdf = self.grp_dark.content_layout()
        dark_row = QHBoxLayout()
        self.btn_cap_dark   = QPushButton("CAP DARK")
        self.btn_cap_dark.setStyleSheet(_BTN_STYLE)
        self.btn_dark_toggle = QPushButton("DARK OFF")
        self.btn_dark_toggle.setCheckable(True)
        self.btn_dark_toggle.setStyleSheet(_BTN_STYLE)
        dark_row.addWidget(self.btn_cap_dark)
        dark_row.addWidget(self.btn_dark_toggle)
        gdf.addLayout(dark_row)
        flat_row = QHBoxLayout()
        self.btn_cap_flat   = QPushButton("CAP FLAT")
        self.btn_cap_flat.setStyleSheet(_BTN_STYLE)
        self.btn_flat_toggle = QPushButton("FLAT OFF")
        self.btn_flat_toggle.setCheckable(True)
        self.btn_flat_toggle.setStyleSheet(_BTN_STYLE)
        flat_row.addWidget(self.btn_cap_flat)
        flat_row.addWidget(self.btn_flat_toggle)
        gdf.addLayout(flat_row)
        layout.addWidget(self.grp_dark)

        # ── Centroid + 통계 상태 표시 ────────────────────────────────
        self.grp_centroid = CollapsibleSection("CENTROID / STATS")
        gc2 = self.grp_centroid.content_layout()
        self.lbl_cx     = QLabel("cX: —")
        self.lbl_cy     = QLabel("cY: —")
        self.lbl_bright = QLabel("Brightness: —")
        self.lbl_fps    = QLabel("FPS: —")
        self.lbl_snr    = QLabel("SNR: —")
        self.lbl_mean   = QLabel("Mean: —")
        self.lbl_sat    = QLabel("")          # 포화 경고
        _stat_style = (
            "color: #ffe66d; font-family: 'Courier New'; "
            "font-size: 12px; font-weight: bold;"
        )
        for lbl in (self.lbl_cx, self.lbl_cy, self.lbl_bright,
                    self.lbl_fps, self.lbl_snr, self.lbl_mean):
            lbl.setStyleSheet(_stat_style)
            gc2.addWidget(lbl)
        self.lbl_sat.setStyleSheet(
            "color: #e94560; font-family: 'Courier New'; "
            "font-size: 11px; font-weight: bold;"
        )
        gc2.addWidget(self.lbl_sat)
        layout.addWidget(self.grp_centroid)

        # ── 온도 그룹 (Picam 전용) ────────────────────────────────────
        self.grp_temp = CollapsibleSection("TEMPERATURE")
        gt = self.grp_temp.content_layout()
        temp_row = QHBoxLayout()
        lbl_sp = QLabel("Setpoint (°C):")
        lbl_sp.setStyleSheet(_LBL_STYLE)
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(-100.0, 50.0)
        self.spin_temp.setValue(-70.0)
        self.spin_temp.setSuffix(" °C")
        self.spin_temp.setStyleSheet(_SPIN_STYLE)
        self.btn_apply_temp = QPushButton("SET")
        self.btn_apply_temp.setStyleSheet(_BTN_STYLE)
        temp_row.addWidget(lbl_sp)
        temp_row.addWidget(self.spin_temp, 1)
        temp_row.addWidget(self.btn_apply_temp)
        gt.addLayout(temp_row)
        self.lbl_temp_status = QLabel("Reading: —")
        self.lbl_temp_status.setStyleSheet(_LBL_STYLE)
        gt.addWidget(self.lbl_temp_status)
        layout.addWidget(self.grp_temp)

        # ── ADC 그룹 (Picam 전용) ─────────────────────────────────────
        self.grp_adc = CollapsibleSection("ADC SETTINGS")
        gadc = self.grp_adc.content_layout()
        self._adc_combos = {}
        for key, label in [
            ("adc_quality",    "Quality"),
            ("adc_speed",      "Speed"),
            ("adc_analog_gain","Gain"),
            ("bit_depth",      "Bit Depth"),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet(_LBL_STYLE)
            lbl.setFixedWidth(95)
            cb = QComboBox()
            cb.setStyleSheet("""
                QComboBox { background: #080e1e; border: 1px solid #0f3460;
                    color: #c0d0ff; border-radius: 3px;
                    font-family: 'Courier New'; font-size: 11px; padding: 2px 4px; }
                QComboBox QAbstractItemView { background: #0f1729; color: #c0d0ff; }
            """)
            row.addWidget(lbl)
            row.addWidget(cb, 1)
            gadc.addLayout(row)
            self._adc_combos[key] = cb
        self.btn_apply_adc = QPushButton("APPLY ADC")
        self.btn_apply_adc.setStyleSheet(_BTN_STYLE)
        gadc.addWidget(self.btn_apply_adc)
        layout.addWidget(self.grp_adc)

        # ── 공간 필터 그룹 (소프트웨어, 모든 카메라) ─────────────────
        self.grp_spatial = CollapsibleSection("SPATIAL FILTER")
        gsf = self.grp_spatial.content_layout()

        # 핫픽셀 제거
        hp_row = QHBoxLayout()
        self.check_hotpx = QCheckBox("Hot Pixel")
        self.check_hotpx.setStyleSheet(_CHECK_STYLE)
        self.spin_hotpx = QSpinBox()
        self.spin_hotpx.setRange(10, 255)
        self.spin_hotpx.setValue(60)
        self.spin_hotpx.setPrefix("thr:")
        self.spin_hotpx.setStyleSheet(_SPIN_STYLE)
        hp_row.addWidget(self.check_hotpx)
        hp_row.addWidget(self.spin_hotpx)
        gsf.addLayout(hp_row)

        # Gaussian blur
        gb_row = QHBoxLayout()
        self.check_gaussian = QCheckBox("Gaussian σ:")
        self.check_gaussian.setStyleSheet(_CHECK_STYLE)
        self.spin_gaussian_sigma = QDoubleSpinBox()
        self.spin_gaussian_sigma.setRange(0.3, 10.0)
        self.spin_gaussian_sigma.setSingleStep(0.1)
        self.spin_gaussian_sigma.setValue(1.0)
        self.spin_gaussian_sigma.setDecimals(1)
        self.spin_gaussian_sigma.setStyleSheet(_SPIN_STYLE)
        gb_row.addWidget(self.check_gaussian)
        gb_row.addWidget(self.spin_gaussian_sigma)
        gsf.addLayout(gb_row)

        # Median filter
        med_row = QHBoxLayout()
        self.check_median = QCheckBox("Median k:")
        self.check_median.setStyleSheet(_CHECK_STYLE)
        self.spin_median_k = QSpinBox()
        self.spin_median_k.setRange(3, 5)
        self.spin_median_k.setSingleStep(2)
        self.spin_median_k.setValue(3)
        self.spin_median_k.setStyleSheet(_SPIN_STYLE)
        med_row.addWidget(self.check_median)
        med_row.addWidget(self.spin_median_k)
        gsf.addLayout(med_row)
        layout.addWidget(self.grp_spatial)

        # ── Display 스트레칭 그룹 ─────────────────────────────────────
        self.grp_stretch = CollapsibleSection("DISPLAY STRETCH")
        gst = self.grp_stretch.content_layout()
        self.combo_stretch = QComboBox()
        self.combo_stretch.addItems(["Normalize", "Percentile", "Manual"])
        self.combo_stretch.setStyleSheet("""
            QComboBox { background: #080e1e; border: 1px solid #0f3460;
                color: #c0d0ff; border-radius: 3px;
                font-family: 'Courier New'; font-size: 11px; padding: 2px 4px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #0f1729; color: #c0d0ff; }
        """)
        gst.addWidget(self.combo_stretch)
        # Percentile row
        self.grp_stretch_pct = QWidget()
        pct_row = QHBoxLayout()
        self.grp_stretch_pct.setLayout(pct_row)
        pct_row.setContentsMargins(0, 0, 0, 0)
        lbl_lo = QLabel("Lo%:"); lbl_lo.setStyleSheet(_LBL_STYLE)
        self.spin_pct_lo = QDoubleSpinBox()
        self.spin_pct_lo.setRange(0.0, 49.0)
        self.spin_pct_lo.setSingleStep(0.5)
        self.spin_pct_lo.setValue(0.5)
        self.spin_pct_lo.setDecimals(1)
        self.spin_pct_lo.setStyleSheet(_SPIN_STYLE)
        lbl_hi = QLabel("Hi%:"); lbl_hi.setStyleSheet(_LBL_STYLE)
        self.spin_pct_hi = QDoubleSpinBox()
        self.spin_pct_hi.setRange(51.0, 100.0)
        self.spin_pct_hi.setSingleStep(0.5)
        self.spin_pct_hi.setValue(99.5)
        self.spin_pct_hi.setDecimals(1)
        self.spin_pct_hi.setStyleSheet(_SPIN_STYLE)
        pct_row.addWidget(lbl_lo); pct_row.addWidget(self.spin_pct_lo)
        pct_row.addWidget(lbl_hi); pct_row.addWidget(self.spin_pct_hi)
        self.grp_stretch_pct.setVisible(False)
        gst.addWidget(self.grp_stretch_pct)
        # Manual row
        self.grp_stretch_man = QWidget()
        man_row = QHBoxLayout()
        self.grp_stretch_man.setLayout(man_row)
        man_row.setContentsMargins(0, 0, 0, 0)
        lbl_mn = QLabel("Min:"); lbl_mn.setStyleSheet(_LBL_STYLE)
        self.spin_man_min = QDoubleSpinBox()
        self.spin_man_min.setRange(0, 65535)
        self.spin_man_min.setValue(0)
        self.spin_man_min.setStyleSheet(_SPIN_STYLE)
        lbl_mx = QLabel("Max:"); lbl_mx.setStyleSheet(_LBL_STYLE)
        self.spin_man_max = QDoubleSpinBox()
        self.spin_man_max.setRange(1, 65535)
        self.spin_man_max.setValue(65535)
        self.spin_man_max.setStyleSheet(_SPIN_STYLE)
        man_row.addWidget(lbl_mn); man_row.addWidget(self.spin_man_min)
        man_row.addWidget(lbl_mx); man_row.addWidget(self.spin_man_max)
        self.grp_stretch_man.setVisible(False)
        gst.addWidget(self.grp_stretch_man)
        layout.addWidget(self.grp_stretch)

        layout.addStretch()

        # ── 시그널 연결 ───────────────────────────────────────────────
        self.btn_scan.clicked.connect(self.camera_scan_requested)
        self.btn_connect.clicked.connect(
            lambda: self.camera_connect_requested.emit(self.camera_list.currentRow())
        )
        self.btn_disconnect.clicked.connect(self.camera_disconnect_requested)
        self.btn_start.clicked.connect(self.camera_start_requested)
        self.btn_stop.clicked.connect(self.camera_stop_requested)
        self.btn_snap.clicked.connect(self.snap_requested)
        self.check_gil_block.toggled.connect(
            lambda checked: setattr(self._cam, 'simulate_gil_block', checked) if self._cam else None
        )
        self.btn_apply_exp.clicked.connect(self._apply_exposure)
        self.btn_apply_fps.clicked.connect(self._apply_fps)
        self.spin_avg.valueChanged.connect(self._apply_avg)
        self.spin_bin_thresh.valueChanged.connect(self._apply_bin_params)
        self.check_bin.toggled.connect(self._apply_bin_params)
        self._thresh_btn_grp.buttonClicked.connect(lambda _: self._apply_bin_params())
        self.check_show_bin.toggled.connect(self._apply_show_bin)
        self.check_log.toggled.connect(self._apply_log)
        self.spin_log.valueChanged.connect(self._apply_log)
        self.btn_cap_bg.clicked.connect(self.bg_capture_requested)
        self.btn_bg_toggle.toggled.connect(self._apply_bg_sub)
        self.check_tdiff.toggled.connect(self._apply_tdiff)
        self.btn_cap_dark.clicked.connect(self._capture_dark)
        self.btn_dark_toggle.toggled.connect(self._apply_dark)
        self.btn_cap_flat.clicked.connect(self._capture_flat)
        self.btn_flat_toggle.toggled.connect(self._apply_flat)
        self.btn_apply_temp.clicked.connect(self._apply_temperature)
        self.btn_apply_adc.clicked.connect(self._apply_adc)
        # 시간 축 모드 — 기본값: Live (Single)
        self.combo_temporal.setCurrentIndex(5)   # TemporalMode.SINGLE
        self._apply_temporal_mode(5)             # proc에 즉시 반영
        self.combo_temporal.currentIndexChanged.connect(self._apply_temporal_mode)
        self.btn_reset_accum.clicked.connect(lambda: self._proc.reset_accum())
        # centroid 모드
        self._centroid_mode_grp.buttonClicked.connect(
            lambda _: self._apply_centroid_mode())
        # 공간 필터
        self.check_hotpx.toggled.connect(self._apply_spatial)
        self.spin_hotpx.valueChanged.connect(self._apply_spatial)
        self.check_gaussian.toggled.connect(self._apply_spatial)
        self.spin_gaussian_sigma.valueChanged.connect(self._apply_spatial)
        self.check_median.toggled.connect(self._apply_spatial)
        self.spin_median_k.valueChanged.connect(self._apply_spatial)
        # display 스트레칭
        self.combo_stretch.currentIndexChanged.connect(self._apply_stretch)
        self.spin_pct_lo.valueChanged.connect(self._apply_stretch)
        self.spin_pct_hi.valueChanged.connect(self._apply_stretch)
        self.spin_man_min.valueChanged.connect(self._apply_stretch)
        self.spin_man_max.valueChanged.connect(self._apply_stretch)

    # ── 카메라 어태치 / 디태치 ────────────────────────────────────────

    def attach_camera(self, cam: BaseCamera):
        self._cam = cam
        self._caps = cam.capabilities
        self._apply_capabilities(self._caps)
        self._set_connected(True)
        
        from core.camera.simulated import SimulatedCamera
        if isinstance(cam, SimulatedCamera):
            self.check_gil_block.setVisible(True)
            self.check_gil_block.setChecked(getattr(cam, 'simulate_gil_block', False))
        else:
            self.check_gil_block.setVisible(False)

        # 현재 카메라 값 읽어 UI 반영
        try:
            self.spin_exposure.setValue(cam.get_exposure_ms())
        except Exception:
            pass

        if self._caps.has_temperature:
            try:
                mn, mx = self._caps.temperature_range_c
                if mn is not None:
                    self.spin_temp.setMinimum(mn)
                if mx is not None:
                    self.spin_temp.setMaximum(mx)
            except Exception:
                pass
            # setpoint 읽기: SDK가 25°C로 리셋했으면 저장된 마지막값 복원
            try:
                reading, setpoint, status = cam.get_temperature()
                saved_sp = QSettings("SpeAnalyze", "CameraPanel").value(
                    "last_temp_setpoint", None, type=float
                )
                if saved_sp is not None and (setpoint is None or abs(float(setpoint) - 25.0) < 0.5):
                    # SDK가 25°C로 초기화했으면 이전 설정값 복원
                    self.spin_temp.setValue(saved_sp)
                    self.log_message.emit(
                        f"🌡 온도 Setpoint 복원: {saved_sp:.1f}°C → 적용 중..."
                    )
                    self._run_sdk(
                        lambda sp=saved_sp: cam.set_temperature(sp),
                        lambda _: None,
                        "온도 복원 오류",
                    )
                elif setpoint is not None:
                    self.spin_temp.setValue(float(setpoint))
            except Exception:
                pass
            self._temp_thread = TempPollerThread(cam, 3000)
            self._temp_thread.temp_read.connect(self._on_temp_read)
            self._temp_thread.start()

        if self._caps.has_fps_control:
            try:
                mn, mx = self._caps.fps_range
                self.spin_fps.setRange(mn, mx)
                current_fps = cam.get_fps()
                self.spin_fps.setValue(current_fps)
            except Exception:
                pass

        if self._caps.has_adc:
            self._adc_combos["adc_quality"].addItems(
                [str(x) for x in self._caps.adc_quality_options]
            )
            self._adc_combos["adc_speed"].addItems(
                [str(x) for x in self._caps.adc_speed_options]
            )
            self._adc_combos["adc_analog_gain"].addItems(
                [str(x) for x in self._caps.adc_gain_options]
            )
            self._adc_combos["bit_depth"].addItems(
                [str(x) for x in self._caps.adc_bit_depth_options]
            )
            # 현재 카메라에 적용된 ADC 값으로 콤보박스 선택
            try:
                current_adc = cam.get_adc_settings()
                for key, val in current_adc.items():
                    cb = self._adc_combos.get(key)
                    if cb is None or val is None:
                        continue
                    idx = cb.findText(str(val))
                    if idx >= 0:
                        cb.setCurrentIndex(idx)
            except Exception:
                pass

    def detach_camera(self):
        if self._temp_thread is not None:
            self._temp_thread.stop()
            self._temp_thread = None
        self._cam = None
        self._caps = None
        self._set_connected(False)
        self.lbl_temp_status.setText("Reading: —")
        for cb in self._adc_combos.values():
            cb.clear()

    def _run_sdk(self, fn, on_ok=None, err_label: str = "오류", btn=None):
        """SDK 함수를 QThread에서 실행 — 호출 즉시 반환, UI 블로킹 없음.
        btn: 실행 중 비활성화할 버튼 (완료 시 자동 복원)."""
        if self._cmd_thread is not None and self._cmd_thread.isRunning():
            self.log_message.emit("⚠️ 이전 명령 실행 중 — 잠시 후 재시도")
            return
        if btn:
            btn.setEnabled(False)
        t = QThread()
        w = _CamCommandWorker(fn)
        self._cmd_thread, self._cmd_worker = t, w
        w.moveToThread(t)
        t.started.connect(w.run)
        if on_ok:
            w.success.connect(on_ok)
        w.error.connect(lambda e: self.log_message.emit(f"❌ {err_label}: {e}"))
        w.success.connect(lambda _: t.quit())
        w.error.connect(lambda _: t.quit())
        if btn:
            t.finished.connect(lambda: btn.setEnabled(self._cam is not None))
        t.start()

    def _apply_capabilities(self, caps: CameraCapabilities):
        self.grp_fps.setVisible(caps.has_fps_control)
        self.grp_proc.setVisible(caps.has_binarize)
        self.grp_log.setVisible(caps.has_log_scale)
        self.grp_bg.setVisible(caps.has_bg_subtraction)
        self.grp_centroid.setVisible(caps.has_centroid)
        self.grp_temp.setVisible(caps.has_temperature)
        self.grp_adc.setVisible(caps.has_adc)
        # 소프트웨어 공통 그룹 (기본 True)
        self.grp_dark.setVisible(getattr(caps, 'has_dark_flat', True))
        self.grp_spatial.setVisible(getattr(caps, 'has_spatial_filter', True))
        self.grp_stretch.setVisible(getattr(caps, 'has_display_stretch', True))

    def _set_connected(self, connected: bool):
        self.btn_connect.setEnabled(not connected)
        self.btn_disconnect.setEnabled(connected)
        self.btn_start.setEnabled(connected)
        self.btn_stop.setEnabled(False)
        self.btn_snap.setEnabled(connected)
        self.btn_apply_exp.setEnabled(connected)
        if not connected:
            self.grp_fps.setVisible(False)
            self.grp_proc.setVisible(False)
            self.grp_log.setVisible(False)
            self.grp_bg.setVisible(False)
            self.grp_centroid.setVisible(False)
            self.grp_temp.setVisible(False)
            self.grp_adc.setVisible(False)
            self.grp_dark.setVisible(False)
            self.grp_spatial.setVisible(False)
            self.grp_stretch.setVisible(False)

    def set_grabbing(self, grabbing: bool):
        self.btn_start.setEnabled(not grabbing)
        self.btn_stop.setEnabled(grabbing)
        # 라이브 중에는 snap 비활성 (라이브 정지 후 사용)
        self.btn_snap.setEnabled(not grabbing and self._cam is not None)

    # ── UI → Processor / Camera 적용 ─────────────────────────────────

    def _apply_exposure(self):
        if self._cam is None:
            return
        ms = self.spin_exposure.value()
        def _ok(actual):
            if actual is not None:
                self.spin_exposure.setValue(actual)
                self.exposure_applied.emit(float(actual))
            else:
                self.exposure_applied.emit(float(ms))
            self.log_message.emit(f"Exposure → {ms:.2f} ms")
        self._run_sdk(lambda: self._cam.set_exposure_ms(ms),
                      _ok, "Exposure 오류", self.btn_apply_exp)

    def _apply_fps(self):
        if self._cam is None:
            return
        if self.check_fps_lock.isChecked():
            fps_val = self.spin_fps.value()
            def _ok(actual):
                if actual is not None:
                    self.spin_fps.setValue(actual)
                self.log_message.emit(f"FPS → {fps_val:.1f}")
            self._run_sdk(lambda: self._cam.set_fps(fps_val),
                          _ok, "FPS 오류", self.btn_apply_fps)
        else:
            self._run_sdk(lambda: self._cam.disable_fps_lock(),
                          lambda _: self.log_message.emit("FPS 고정 해제"),
                          "FPS 오류", self.btn_apply_fps)

    def _apply_avg(self, val: int):
        self._proc.avg_n = val

    def _apply_bin_params(self, *_):
        val = self.spin_bin_thresh.value()
        self._proc.bin_enabled = self.check_bin.isChecked()
        self._proc.bin_threshold = float(val)
        checked_id = self._thresh_btn_grp.checkedId()
        if checked_id >= 0:
            self._proc.bin_mode = checked_id

    def _apply_show_bin(self, checked: bool):
        self._proc.show_binary = checked

    def _apply_log(self, *_):
        self._proc.log_enabled = self.check_log.isChecked()
        self._proc.log_level = self.spin_log.value()

    def _apply_bg_sub(self, checked: bool):
        self._proc.bg_sub_enabled = checked
        self.btn_bg_toggle.setText("BG SUB ON" if checked else "BG SUB OFF")

    def _apply_tdiff(self, checked: bool):
        self._proc.temporal_diff_enabled = checked

    def _capture_dark(self):
        self._proc.capture_dark_frame()
        self.log_message.emit("🌑 Dark frame 캡처됨")

    def _apply_dark(self, checked: bool):
        self._proc.dark_enabled = checked and self._proc.dark_frame is not None
        self.btn_dark_toggle.setText("DARK ON" if self._proc.dark_enabled else "DARK OFF")
        if checked and self._proc.dark_frame is None:
            self.log_message.emit("⚠ Dark frame 없음 — 먼저 CAP DARK")

    def _capture_flat(self):
        if not self._proc._buffer:
            self.log_message.emit("⚠ 버퍼 없음 — 카메라 실행 후 Flat field 캡처")
            return
        self._proc.set_flat_field(self._proc._buffer[-1])
        self.log_message.emit("⬜ Flat field 캡처됨")

    def _apply_flat(self, checked: bool):
        self._proc.flat_enabled = checked and self._proc.flat_field is not None
        self.btn_flat_toggle.setText("FLAT ON" if self._proc.flat_enabled else "FLAT OFF")
        if checked and self._proc.flat_field is None:
            self.log_message.emit("⚠ Flat field 없음 — 먼저 CAP FLAT")

    def _apply_temporal_mode(self, idx: int):
        self._proc.temporal_mode = TemporalMode(idx)
        self._proc.reset_buffer()

    def _apply_centroid_mode(self):
        mode_id = self._centroid_mode_grp.checkedId()
        self._proc.centroid_mode = CentroidMode(mode_id)

    def _apply_spatial(self, *_):
        self._proc.hot_pixel_enabled   = self.check_hotpx.isChecked()
        self._proc.hot_pixel_threshold = self.spin_hotpx.value()
        self._proc.gaussian_enabled    = self.check_gaussian.isChecked()
        self._proc.gaussian_sigma      = self.spin_gaussian_sigma.value()
        self._proc.median_enabled      = self.check_median.isChecked()
        self._proc.median_ksize        = self.spin_median_k.value()

    def _apply_stretch(self, *_):
        idx = self.combo_stretch.currentIndex()
        self._proc.display_stretch = DisplayStretch(idx)
        self.grp_stretch_pct.setVisible(idx == DisplayStretch.PERCENTILE)
        self.grp_stretch_man.setVisible(idx == DisplayStretch.MANUAL)
        if idx == DisplayStretch.PERCENTILE:
            self._proc.display_percentile_lo = self.spin_pct_lo.value()
            self._proc.display_percentile_hi = self.spin_pct_hi.value()
        elif idx == DisplayStretch.MANUAL:
            self._proc.display_min = self.spin_man_min.value()
            self._proc.display_max = self.spin_man_max.value()

    def _apply_temperature(self):
        if self._cam is None:
            return
        requested = self.spin_temp.value()
        def _do():
            self._cam.set_temperature(requested)
            return self._cam.get_temperature()   # (reading, setpoint, status)
        def _ok(result):
            reading, setpoint, status = result
            if setpoint is None:
                setpoint = requested
            self.update_temperature_display(reading, setpoint, status)
            confirmed = float(setpoint)
            # 성공한 setpoint를 QSettings에 저장 (다음 연결 시 자동 복원)
            QSettings("SpeAnalyze", "CameraPanel").setValue(
                "last_temp_setpoint", confirmed
            )
            if abs(confirmed - requested) > 0.1:
                self.log_message.emit(
                    f"⚠ SP 요청 {requested:.1f}°C → 카메라 확인 {confirmed:.1f}°C (범위 클램프됨)"
                )
            else:
                self.log_message.emit(
                    f"✓ SP 설정됨: {confirmed:.1f}°C  (Reading: {float(reading):.1f}°C  |  {status})"
                )
        self._run_sdk(_do, _ok, "Temperature 오류", self.btn_apply_temp)

    def _apply_adc(self):
        if self._cam is None:
            return
        kwargs = {key: cb.currentText() for key, cb in self._adc_combos.items()
                  if cb.currentText()}
        self._run_sdk(
            lambda: self._cam.set_adc_settings(**kwargs),
            lambda _: self.log_message.emit(f"ADC 설정 적용: {list(kwargs.keys())}"),
            "ADC 오류", self.btn_apply_adc
        )

    # ── 실시간 상태 갱신 ─────────────────────────────────────────────

    def update_centroid(self, cx: Optional[float], cy: Optional[float],
                        brightness: int, fps: float,
                        snr: float = 0.0, mean: float = 0.0,
                        saturated: bool = False, sat_ratio: float = 0.0):
        self.lbl_cx.setText(f"cX: {cx:.1f}" if cx is not None else "cX: —")
        self.lbl_cy.setText(f"cY: {cy:.1f}" if cy is not None else "cY: —")
        self.lbl_bright.setText(f"Brightness: {brightness}")
        self.lbl_fps.setText(f"FPS: {fps:.1f}")
        self.lbl_snr.setText(f"SNR: {snr:.1f}")
        self.lbl_mean.setText(f"Mean: {mean:.1f}")
        if saturated:
            self.lbl_sat.setText(f"⚠ SATURATED {sat_ratio*100:.2f}%")
        else:
            self.lbl_sat.setText("")

    def _on_temp_read(self, reading, setpoint, status):
        """TempPollerThread 시그널 수신 → UI 갱신 (메인 스레드)."""
        if setpoint is None:
            setpoint = self.spin_temp.value()
        self.update_temperature_display(reading, setpoint, status)

    def update_temperature_display(self, reading, setpoint=None, status=None):
        parts = []
        parts.append(f"Reading: {float(reading):.1f}°C" if reading is not None else "Reading: —")
        if setpoint is not None:
            parts.append(f"SP: {float(setpoint):.1f}°C")
        if status is not None:
            parts.append(str(status))
        self.lbl_temp_status.setText("  |  ".join(parts))

    def get_selected_camera_type(self) -> str:
        return self.combo_cam_type.currentText()

    def populate_camera_list(self, items: List[str]):
        self.camera_list.clear()
        for item in items:
            self.camera_list.addItem(item)
        if items:
            self.camera_list.setCurrentRow(0)
