"""
ui/acquisition/acquisition_tab.py
Picam 배치 획득 탭.

카메라 인스턴스는 LiveTab에서 공유받는다 (자체 연결/해제 없음).
- set_shared_camera(cam) : LiveTab 연결 완료 시 호출
- clear_shared_camera()  : LiveTab 연결 해제 시 호출

획득 시작 시 acquisition_starting 시그널 → MainWindow → LiveTab.stop_live().
획득 완료 시 spe_saved(path) 시그널 → MainWindow → Analysis 탭 자동 오픈.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
    QComboBox, QCheckBox, QLineEdit, QProgressBar,
    QTextEdit, QFileDialog, QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt, QThread, QObject, QSettings, QTimer, pyqtSignal

from core.async_worker import TempPollerThread
from core.camera.base import BaseCamera
from core.camera.picamp import PicamCamera
from core.spe_writer import save_spe
from ui.image_viewer import ImageViewer   # #15 프리뷰
from theme.styles import (
    Fonts, Sizes,
    C_ACCENT, C_DANGER, C_WARN, C_TEXT, C_TEXT_DIM, C_TEXT_DEAD, C_INFO,
    C_BG_DEEP, C_BORDER,
    BTN_PRIMARY, BTN_SMALL,
    SPIN_STYLE, COMBO_STYLE, EDIT_STYLE, TEXTEDIT_LOG,
    PROGRESS_STYLE, CHECKBOX_STYLE,
    grp_style, lbl, log_html,
)
from ui.widgets.collapsible_section import CollapsibleSection

# 하위 호환용 로컬 별칭 (기존 코드에서 쓰던 이름 그대로)
_FC       = Fonts.MONO
_FS_TITLE = Sizes.TITLE
_FS_BTN   = Sizes.BTN
_FS_CTRL  = Sizes.CTRL
_FS_LOG   = Sizes.LOG
_FS_SMALL = Sizes.SMALL
_LBL      = lbl()
_SPIN     = SPIN_STYLE
_BTN      = BTN_SMALL




class _AcqWorker(QObject):
    """백그라운드 프레임 획득 워커 (Picam 전용).

    설정 적용(노출/온도/ADC)과 온도 Lock 대기를 모두 백그라운드에서 처리해
    메인 스레드를 블로킹하지 않는다.
    """
    progress    = pyqtSignal(int, int)   # (현재, 전체)
    frame_ready = pyqtSignal(object)     # 프리뷰용 개별 프레임
    finished    = pyqtSignal(list)       # frames list
    error       = pyqtSignal(str)
    log_message = pyqtSignal(str)        # 워커 → UI 로그

    def __init__(self, cam: PicamCamera, n_frames: int, timeout_s: float,
                 setup_exposure_ms: Optional[float] = None,
                 setup_temp_c: Optional[float] = None,
                 wait_temp_lock: bool = False,
                 adc_kwargs: Optional[dict] = None):
        super().__init__()
        self._cam             = cam
        self._n               = n_frames
        self._timeout         = timeout_s
        self._setup_exposure  = setup_exposure_ms
        self._setup_temp      = setup_temp_c
        self._wait_temp_lock  = wait_temp_lock
        self._adc_kwargs      = adc_kwargs or {}

    def run(self):
        try:
            # ── 1. 설정 적용 (백그라운드 — 메인 스레드 차단 없음) ───────
            if self._setup_exposure is not None:
                try:
                    self._cam.set_exposure_ms(self._setup_exposure)
                except Exception as e:
                    self.log_message.emit(f"⚠️ 노출 설정 실패: {e}")

            if self._adc_kwargs:
                try:
                    self._cam.set_adc_settings(**self._adc_kwargs)
                    self.log_message.emit(f"ADC 설정: {list(self._adc_kwargs.keys())}")
                except Exception as e:
                    self.log_message.emit(f"⚠️ ADC 설정 실패: {e}")

            if self._setup_temp is not None:
                try:
                    self._cam.set_temperature(self._setup_temp)
                    self.log_message.emit(f"온도 setpoint: {self._setup_temp:.1f}°C")
                except Exception as e:
                    self.log_message.emit(f"⚠️ 온도 설정 실패: {e}")

            # ── 2. 온도 Lock 대기 (최대 120초 — 반드시 백그라운드에서) ──
            if self._wait_temp_lock:
                self.log_message.emit("🌡 온도 Lock 대기 중… (최대 120초)")
                try:
                    self._cam._wrapper.wait_temperature_lock(timeout_s=120)
                    self.log_message.emit("✅ 온도 Lock 완료")
                except Exception as e:
                    self.error.emit(f"온도 Lock 실패: {e}")
                    return

            # ── 3. 프레임 획득 ─────────────────────────────────────────
            frames = []
            for i in range(self._n):
                batch = self._cam._wrapper.acquire_images(1, timeout_s=self._timeout)
                if not batch:
                    raise RuntimeError(f"프레임 {i+1} 획득 실패 (빈 결과)")
                frame = batch[0]
                frames.append(frame)
                self.frame_ready.emit(frame)
                self.progress.emit(i + 1, self._n)
            self.finished.emit(frames)
        except Exception as e:
            self.error.emit(str(e))


class AcquisitionTab(QWidget):
    """
    Picam 배치 획득 탭.

    카메라는 LiveTab에서 공유받으며 자체적으로 연결/해제하지 않는다.
    획득 시작 전 acquisition_starting → MainWindow → LiveTab.stop_live().
    획득 완료 후 spe_saved(path) → MainWindow → Analysis 탭 자동 오픈.
    """

    spe_saved            = pyqtSignal(str)   # 저장된 SPE 경로
    log_message          = pyqtSignal(str)
    acquisition_starting = pyqtSignal()      # 라이브 스트림 정지 요청
    acquisition_done     = pyqtSignal()      # 획득 완료/오류 — 라이브 재개 요청
    exposure_changed     = pyqtSignal(float) # 노출 UI 변경 (Live 탭과 동기화)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam: Optional[BaseCamera] = None
        self._worker: Optional[_AcqWorker] = None
        self._thread: Optional[QThread] = None
        self._acq_start_time: float = 0.0                     # ETA 계산용
        self._acq_cur_frame: int = 0
        self._acq_total_frames: int = 0
        self._frame_exposure_s: float = 0.0
        self._frame_readout_s: float = 0.0
        self._frame_delta_s: float = 0.0
        self._frame_model_s: float = 0.0
        self._acq_expected_total_s: float = 0.0
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self._on_progress_tick)
        self._temp_thread: Optional[TempPollerThread] = None  # 온도 폴링 (백그라운드)
        self._bg_frames: Optional[np.ndarray] = None  # (N, H, W) float32
        self._build_ui()

    def _estimate_frame_timing(self, exposure_ms: float, timeout_s: float) -> tuple[float, float, float, float]:
        """프레임 시간 모델(Exposure + Readout + Delta)을 계산한다.

        - Exposure: UI 설정값(ms)
        - Readout : Picam wrapper 계산값 우선 사용
        - Delta   : 스레드/큐/호출 오버헤드 완충(보수적 상수 범위)
        """
        exp_s = max(float(exposure_ms) / 1000.0, 0.0)
        readout_s = 0.0

        if isinstance(self._cam, PicamCamera):
            try:
                total_s = float(self._cam._wrapper._get_frame_total_s())
                readout_s = max(total_s - exp_s, 0.0)
            except Exception:
                readout_s = 0.0

        # 타임아웃 여유를 과대 반영하지 않도록 작은 상한(50ms) 적용
        slack = max(float(timeout_s) - (exp_s + readout_s), 0.0)
        delta_s = min(slack, 0.05)
        # 너무 작은 값으로 0에 수렴하지 않도록 최소 오버헤드 5ms
        delta_s = max(delta_s, 0.005)

        frame_s = exp_s + readout_s + delta_s
        return exp_s, readout_s, delta_s, frame_s

    def _format_duration(self, sec: float) -> str:
        sec = max(float(sec), 0.0)
        if sec < 60:
            return f"{sec:.1f}초"
        if sec < 3600:
            return f"{sec/60:.1f}분 ({sec:.0f}초)"
        return f"{sec/3600:.2f}시간"

    def _update_progress_ui(self, cur: int, total: int):
        elapsed = time.monotonic() - self._acq_start_time
        expected_total = max(self._acq_expected_total_s, 1e-6)
        frame_ratio = (cur / total) if total > 0 else 0.0
        time_ratio = min(elapsed / expected_total, 1.0)
        ratio = min(max(frame_ratio, time_ratio), 1.0)
        self.progress_bar.setValue(int(ratio * 1000))

        remaining = max(expected_total - elapsed, 0.0)
        self.lbl_eta.setText(
            f"⏱ {cur}/{total} 완료  |  남음 {remaining:.1f}s  ({elapsed:.1f}s 경과)"
            f"  |  1프레임={self._frame_model_s*1000:.1f}ms"
        )

    def _on_progress_tick(self):
        if not self.progress_bar.isVisible() or self._acq_total_frames <= 0:
            return
        self._update_progress_ui(self._acq_cur_frame, self._acq_total_frames)

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── 왼쪽: 설정 패널 ───────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(6)

        title = QLabel("PICAM ACQUISITION")
        title.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: {_FS_TITLE};"
            "font-weight: bold; letter-spacing: 4px;"
        )
        left.addWidget(title)

        # ── 공유 카메라 상태 배너 ──────────────────────────────────────
        self._cam_banner = QFrame()
        self._cam_banner.setFrameShape(QFrame.Shape.StyledPanel)
        self._cam_banner.setStyleSheet(
            "QFrame { background: #080e1e; border: 1px solid #0f3460; border-radius: 4px; padding: 2px; }"
        )
        banner_row = QHBoxLayout(self._cam_banner)
        banner_row.setContentsMargins(8, 4, 8, 4)
        lbl_cam_icon = QLabel("📷")
        banner_row.addWidget(lbl_cam_icon)
        self.lbl_cam_status = QLabel("카메라 미연결 — Live 탭에서 먼저 연결하세요")
        self.lbl_cam_status.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: {_FS_CTRL};"
        )
        banner_row.addWidget(self.lbl_cam_status, 1)
        # #16 온도 실시간 표시
        self.lbl_temp_live = QLabel("🌡 —")
        self.lbl_temp_live.setStyleSheet(
            f"color: #a0c8ff; font-family: '{_FC}'; font-size: {_FS_SMALL};"
        )
        self.lbl_temp_live.setVisible(False)
        banner_row.addWidget(self.lbl_temp_live)
        left.addWidget(self._cam_banner)

        # Picam 전용 경고 (HIKVISION 연결 시 표시)
        self.lbl_picam_warn = QLabel("⚠️  Acquisition은 Picam 전용입니다")
        self.lbl_picam_warn.setStyleSheet(
            f"color: #ffe66d; font-family: '{_FC}'; font-size: {_FS_CTRL}; padding: 2px 0;"
        )
        self.lbl_picam_warn.setVisible(False)
        left.addWidget(self.lbl_picam_warn)

        # 노출 / 프레임 수
        grp_acq = CollapsibleSection("ACQUISITION", accent=C_ACCENT)
        ga = grp_acq.content_layout()
        for label, attr, default, rng in [
            ("Exposure (ms):", "spin_exposure", 100.0, (0.001, 3_600_000.0)),
            ("Frames:",        "spin_frames",   10,    (1,     10000)),
            ("Timeout (s):",   "spin_timeout",  30.0,  (1.0,   600.0)),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(_LBL)
            lbl.setFixedWidth(110)
            if "." in str(default):
                w = QDoubleSpinBox()
                w.setRange(*rng)
                w.setValue(default)
                w.setDecimals(3)
            else:
                w = QSpinBox()
                w.setRange(*[int(x) for x in rng])
                w.setValue(int(default))
            w.setStyleSheet(_SPIN)
            setattr(self, attr, w)
            row.addWidget(lbl)
            row.addWidget(w, 1)
            ga.addLayout(row)
        left.addWidget(grp_acq)

        # 온도 설정 (Picam + has_temperature 시 표시)
        self.grp_temp = CollapsibleSection("TEMPERATURE (선택)", accent=C_WARN, collapsed=True)
        gt = self.grp_temp.content_layout()
        self.check_temp = QCheckBox("온도 설정 활성화")
        self.check_temp.setStyleSheet(
            f"QCheckBox {{ color: #8090a8; font-family: '{_FC}'; font-size: {_FS_CTRL}; }}"
        )
        temp_row = QHBoxLayout()
        lbl_t = QLabel("Setpoint (°C):")
        lbl_t.setStyleSheet(_LBL)
        lbl_t.setFixedWidth(110)
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(-100.0, 50.0)
        self.spin_temp.setValue(-70.0)
        self.spin_temp.setSuffix(" °C")
        self.spin_temp.setStyleSheet(_SPIN)
        self.check_wait_lock = QCheckBox("Lock 대기")
        self.check_wait_lock.setStyleSheet(
            f"QCheckBox {{ color: #8090a8; font-family: '{_FC}'; font-size: {_FS_CTRL}; }}"
        )
        self.lbl_temp_reading = QLabel("Reading: —")
        self.lbl_temp_reading.setStyleSheet(
            f"color: #a0c8ff; font-family: '{_FC}'; font-size: {_FS_CTRL};"
        )
        temp_row.addWidget(lbl_t)
        temp_row.addWidget(self.spin_temp, 1)
        gt.addWidget(self.check_temp)
        gt.addLayout(temp_row)
        gt.addWidget(self.lbl_temp_reading)
        gt.addWidget(self.check_wait_lock)
        self.grp_temp.setVisible(False)
        left.addWidget(self.grp_temp)

        # ADC 설정 (Picam + has_adc 시 표시)
        self.grp_adc = CollapsibleSection("ADC (선택)", accent=C_WARN, collapsed=True)
        gadc = self.grp_adc.content_layout()
        self._adc_combos: dict = {}
        for key, label in [
            ("adc_quality",     "Quality"),
            ("adc_speed",       "Speed"),
            ("adc_analog_gain", "Gain"),
            ("bit_depth",       "Bit Depth"),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(f"{label}:")
            lbl.setStyleSheet(_LBL)
            lbl.setFixedWidth(80)
            cb = QComboBox()
            cb.addItem("(default)")
            cb.setStyleSheet(f"""
                QComboBox {{ background: #080e1e; border: 1px solid #0f3460; color: #c0d0ff;
                    border-radius: 3px; font-family: '{_FC}'; font-size: {_FS_CTRL}; padding: 2px 4px; }}
                QComboBox QAbstractItemView {{ background: #0f1729; color: #c0d0ff; }}
            """)
            row.addWidget(lbl)
            row.addWidget(cb, 1)
            gadc.addLayout(row)
            self._adc_combos[key] = cb
        self.grp_adc.setVisible(False)
        left.addWidget(self.grp_adc)

        # 저장 경로
        grp_save = CollapsibleSection("SAVE", accent=C_ACCENT)
        gs = grp_save.content_layout()
        path_row = QHBoxLayout()
        lbl_dir = QLabel("Dir:")
        lbl_dir.setStyleSheet(_LBL)
        self.edit_save_dir = QLineEdit("acquisitions")
        self.edit_save_dir.setStyleSheet(f"""
            QLineEdit {{ background: #080e1e; border: 1px solid #0f3460; color: #c0d0ff;
                border-radius: 3px; font-family: '{_FC}'; font-size: {_FS_CTRL}; padding: 2px 4px; }}
        """)
        btn_browse = QPushButton("…")
        btn_browse.setFixedWidth(30)
        btn_browse.setStyleSheet(_BTN)
        btn_browse.clicked.connect(self._browse_dir)
        path_row.addWidget(lbl_dir)
        path_row.addWidget(self.edit_save_dir, 1)
        path_row.addWidget(btn_browse)
        gs.addLayout(path_row)

        self.check_auto_open = QCheckBox("획득 후 Analysis 탭에서 자동으로 열기")
        self.check_auto_open.setChecked(True)
        self.check_auto_open.setStyleSheet(
            f"QCheckBox {{ color: #8090a8; font-family: '{_FC}'; font-size: {_FS_CTRL}; }}"
        )
        gs.addWidget(self.check_auto_open)
        left.addWidget(grp_save)

        # ── 배경 그룹 ──────────────────────────────────────────────────
        grp_bg = CollapsibleSection("BACKGROUND", accent=C_WARN)
        gbg = grp_bg.content_layout()
        bg_btn_row = QHBoxLayout()
        self.btn_bg_capture = QPushButton("📸  BG 획득")
        self.btn_bg_capture.setStyleSheet(_BTN)
        self.btn_bg_capture.setEnabled(False)
        self.btn_bg_capture.clicked.connect(self._capture_bg)
        self.btn_bg_load = QPushButton("📂  파일 선택")
        self.btn_bg_load.setStyleSheet(_BTN)
        self.btn_bg_load.clicked.connect(self._load_bg_file)
        bg_btn_row.addWidget(self.btn_bg_capture)
        bg_btn_row.addWidget(self.btn_bg_load)
        gbg.addLayout(bg_btn_row)
        bg_sub_row = QHBoxLayout()
        self.check_bg_sub = QCheckBox("BG 차감")
        self.check_bg_sub.setEnabled(False)
        self.check_bg_sub.setStyleSheet(
            f"QCheckBox {{ color: #8090a8; font-family: '{_FC}'; font-size: {_FS_CTRL}; }}"
        )
        self._lbl_bg_status = QLabel("없음")
        self._lbl_bg_status.setStyleSheet(
            f"color: #4a5a7a; font-family: '{_FC}'; font-size: {_FS_SMALL};"
        )
        bg_sub_row.addWidget(self.check_bg_sub)
        bg_sub_row.addWidget(self._lbl_bg_status, 1)
        gbg.addLayout(bg_sub_row)
        left.addWidget(grp_bg)

        # 획득 시작 버튼
        self.btn_acquire = QPushButton("▶  START ACQUISITION")
        self.btn_acquire.setFixedHeight(48)
        self.btn_acquire.setEnabled(False)
        self.btn_acquire.setStyleSheet(f"""
            QPushButton {{ background: #0d2820; color: #4ecdc4; border: 1px solid #1a5040;
                border-radius: 4px; font-family: '{_FC}'; font-weight: bold; font-size: {_FS_BTN}; }}
            QPushButton:hover {{ background: #1a4838; }}
            QPushButton:disabled {{ color: #1a2840; background: #080e1e; }}
        """)
        left.addWidget(self.btn_acquire)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: #080e1e; border: 1px solid #0f3460; border-radius: 3px;
                color: #4ecdc4; font-family: '{_FC}'; font-size: {_FS_CTRL}; text-align: center; }}
            QProgressBar::chunk {{ background: #e94560; border-radius: 2px; }}
        """)
        left.addWidget(self.progress_bar)

        # #7 ETA 레이블 (진행 중 남은 시간 표시)
        self.lbl_eta = QLabel("")
        self.lbl_eta.setVisible(False)
        self.lbl_eta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_eta.setStyleSheet(
            f"color: #ffe66d; font-family: '{_FC}'; font-size: {_FS_CTRL};"
            "background: #0a1020; border: 1px solid #1a3050; border-radius: 3px; padding: 3px;"
        )
        left.addWidget(self.lbl_eta)
        left.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(420)
        root.addWidget(left_widget)

        # ── 오른쪽: 프리뷰 (상) + 로그 (하) ─────────────────────────
        right_layout = QVBoxLayout()
        right_layout.setSpacing(4)

        # #15 프리뷰
        lbl_prev = QLabel("PREVIEW")
        lbl_prev.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: {_FS_CTRL};"
            "font-weight: bold; letter-spacing: 2px;"
        )
        right_layout.addWidget(lbl_prev)
        self.preview_viewer = ImageViewer()
        self.preview_viewer.setMinimumHeight(220)
        right_layout.addWidget(self.preview_viewer, 2)

        # 로그
        lbl_log = QLabel("ACQUISITION LOG")
        lbl_log.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: {_FS_CTRL};"
            "font-weight: bold; letter-spacing: 2px;"
        )
        right_layout.addWidget(lbl_log)
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet(f"""
            QTextEdit {{ background: #080e1e; border: 1px solid #0f3460;
                color: #00cc88; font-family: '{_FC}'; font-size: {_FS_LOG}; }}
        """)
        right_layout.addWidget(self.log_display, 1)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        root.addWidget(right_widget, 1)

        # #19 저장 경로 복원
        _s = QSettings("SpeAnalyze", "AcquisitionTab")
        self.edit_save_dir.setText(_s.value("save_dir", "acquisitions"))

        # ── 시그널 연결 ───────────────────────────────────────────────
        self.btn_acquire.clicked.connect(self._start_acquisition)
        self.spin_exposure.valueChanged.connect(self._on_exposure_spin_changed)

    def _on_exposure_spin_changed(self, ms: float):
        """Acquisition 노출 UI 변경을 외부 탭에 전달한다 (카메라 즉시 적용 안 함)."""
        self.exposure_changed.emit(float(ms))

    def set_exposure_ui(self, ms: float):
        """다른 탭에서 노출값 변경 시 Acquisition UI만 업데이트한다."""
        self.spin_exposure.blockSignals(True)
        self.spin_exposure.setValue(float(ms))
        self.spin_exposure.blockSignals(False)

    # ── 공유 카메라 수신 ──────────────────────────────────────────────

    def set_shared_camera(self, cam: BaseCamera):
        """LiveTab에서 카메라 연결 완료 시 MainWindow가 호출."""
        self._cam = cam
        is_picam = isinstance(cam, PicamCamera)

        # 배너 갱신
        try:
            name = cam.camera_name()
        except Exception:
            name = type(cam).__name__
        self.lbl_cam_status.setText(f"● {name}")
        self.lbl_cam_status.setStyleSheet(
            f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FS_CTRL};"
        )
        self.lbl_picam_warn.setVisible(not is_picam)
        self.btn_acquire.setEnabled(is_picam)
        self.btn_bg_capture.setEnabled(True)

        if not is_picam:
            self._log(f"📷 {name} 연결됨 — Acquisition은 Picam 전용")
            return

        # Picam 전용: caps 읽어 온도/ADC 표시
        caps = cam.capabilities
        self.grp_temp.setVisible(caps.has_temperature)
        self.grp_adc.setVisible(caps.has_adc)

        if caps.has_temperature:
            mn, mx = caps.temperature_range_c
            if mn is not None:
                self.spin_temp.setMinimum(mn)
            if mx is not None:
                self.spin_temp.setMaximum(mx)
            # 현재 setpoint를 읽어 스핀박스 초기화
            try:
                reading, setpoint, status = cam.get_temperature()
                if setpoint is not None:
                    self.spin_temp.setValue(float(setpoint))
            except Exception:
                pass
            # 온도 실시간 폴링 (백그라운드 스레드)
            self.lbl_temp_live.setVisible(True)
            self._temp_thread = TempPollerThread(cam, 3000)
            self._temp_thread.temp_read.connect(self._on_temp_read)
            self._temp_thread.start()

        if caps.has_adc:
            for key, opts in [
                ("adc_quality",     caps.adc_quality_options),
                ("adc_speed",       caps.adc_speed_options),
                ("adc_analog_gain", caps.adc_gain_options),
                ("bit_depth",       caps.adc_bit_depth_options),
            ]:
                cb = self._adc_combos[key]
                cb.clear()
                cb.addItem("(default)")
                cb.addItems([str(x) for x in opts])

        try:
            self.spin_exposure.setValue(cam.get_exposure_ms())
        except Exception:
            pass

        self._log(f"✅ 공유 카메라: {name}")

    def clear_shared_camera(self):
        """LiveTab에서 카메라 해제 시 MainWindow가 호출."""
        if self._temp_thread is not None:
            self._temp_thread.stop()
            self._temp_thread = None
        self.lbl_temp_live.setVisible(False)
        self.lbl_temp_live.setText("🌡 —")
        self._cam = None
        self.lbl_cam_status.setText("카메라 미연결 — Live 탭에서 먼저 연결하세요")
        self.lbl_cam_status.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: {_FS_CTRL};"
        )
        self.lbl_picam_warn.setVisible(False)
        self.btn_acquire.setEnabled(False)
        self.btn_bg_capture.setEnabled(False)
        self.grp_temp.setVisible(False)
        self.grp_adc.setVisible(False)
        for cb in self._adc_combos.values():
            cb.clear()
            cb.addItem("(default)")
        self._log("카메라 연결 해제됨")

    # ── 획득 ─────────────────────────────────────────────────────────

    def _start_acquisition(self):
        if self._cam is None or not isinstance(self._cam, PicamCamera):
            return

        # 라이브 스트림 먼저 정지
        self.acquisition_starting.emit()

        caps        = self._cam.capabilities
        n_frames    = self.spin_frames.value()
        timeout     = self.spin_timeout.value()
        exposure_ms = self.spin_exposure.value()

        # 온도 / ADC 파라미터 수집 (설정 적용은 워커 내부에서 수행)
        setup_temp = (self.spin_temp.value()
                      if caps.has_temperature and self.check_temp.isChecked()
                      else None)
        wait_lock  = (caps.has_temperature
                      and self.check_temp.isChecked()
                      and self.check_wait_lock.isChecked())
        adc_kwargs = {}
        if caps.has_adc:
            for key, cb in self._adc_combos.items():
                val = cb.currentText()
                if val and val != "(default)":
                    adc_kwargs[key] = val

        # 프레임 시간 모델: Exposure + Readout + Delta
        exp_s, readout_s, delta_s, frame_s = self._estimate_frame_timing(exposure_ms, timeout)
        total_s = n_frames * frame_s
        eta_str = self._format_duration(total_s)

        self.btn_acquire.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.lbl_eta.setText(
            f"⏱ 예상: {eta_str}  |  0/{n_frames} 완료"
            f"  |  Exp {exp_s*1000:.1f}ms + Read {readout_s*1000:.1f}ms + Δ {delta_s*1000:.1f}ms"
        )
        self.lbl_eta.setVisible(True)
        self._acq_start_time = time.monotonic()   # #7
        self._acq_cur_frame = 0
        self._acq_total_frames = n_frames
        self._frame_exposure_s = exp_s
        self._frame_readout_s = readout_s
        self._frame_delta_s = delta_s
        self._frame_model_s = frame_s
        self._acq_expected_total_s = total_s
        self._progress_timer.start()
        self._log(
            f"▶ 획득 시작: {n_frames} 프레임, "
            f"노출 {exposure_ms:.3f} ms"
        )
        self._log(
            "⏱ 프레임 시간 모델: "
            f"Exp {exp_s*1000:.1f}ms + Readout {readout_s*1000:.1f}ms + Delta {delta_s*1000:.1f}ms "
            f"= {frame_s*1000:.1f}ms/frame"
        )
        self._log(f"⏱ 예상 소요 시간: {eta_str}")

        self._thread = QThread()
        self._worker = _AcqWorker(
            self._cam, n_frames, timeout,
            setup_exposure_ms=exposure_ms,
            setup_temp_c=setup_temp,
            wait_temp_lock=wait_lock,
            adc_kwargs=adc_kwargs,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.frame_ready.connect(self._on_preview_frame)
        self._worker.finished.connect(self._on_acquired)
        self._worker.error.connect(self._on_acq_error)
        self._worker.log_message.connect(self._log)
        self._thread.start()

    def _on_progress(self, cur: int, total: int):
        self._acq_cur_frame = cur
        self._acq_total_frames = total
        self._update_progress_ui(cur, total)
        self._log(f"  Frame {cur}/{total}")

    def _on_acquired(self, frames: list):
        self._progress_timer.stop()
        self._thread.quit()
        self._thread.wait()
        self.progress_bar.setValue(1000)
        self.progress_bar.setVisible(False)
        self.btn_acquire.setEnabled(self._cam is not None and isinstance(self._cam, PicamCamera))
        self.acquisition_done.emit()

        # #7 총 소요 시간 표시
        elapsed = time.monotonic() - self._acq_start_time
        self.lbl_eta.setText(f"✅ 완료  |  총 소요: {elapsed:.1f}초")

        if not frames:
            self._log("❌ 프레임 획득 실패 (빈 결과)")
            return

        self._log(f"✅ {len(frames)} 프레임 획득 완료 (총 {elapsed:.1f}초) → 저장 중...")
        try:
            save_frames = self._subtract_bg_from_list(frames)
            path = self._save_spe(save_frames)
            self._log(f"💾 저장: {path}")
            if self.check_auto_open.isChecked():
                self.spe_saved.emit(str(path))
        except Exception as e:
            self._log(f"❌ 저장 오류: {e}")

    def _on_acq_error(self, msg: str):
        self._progress_timer.stop()
        self._thread.quit()
        self._thread.wait()
        self.progress_bar.setVisible(False)
        self.acquisition_done.emit()
        elapsed = time.monotonic() - self._acq_start_time
        self.lbl_eta.setText(f"❌ 오류  |  {elapsed:.1f}초 후 중단")
        self.btn_acquire.setEnabled(self._cam is not None and isinstance(self._cam, PicamCamera))
        self._log(f"❌ 획득 오류: {msg}")

    # ── #15 프리뷰 ────────────────────────────────────────────────────

    def _on_preview_frame(self, frame: np.ndarray):
        """워커 스레드에서 각 프레임 수신 → 프리뷰 갱신."""
        self.preview_viewer.set_image(self._apply_bg(frame))

    # ── #16 온도 폴링 ─────────────────────────────────────────────────

    def _on_temp_read(self, reading, setpoint, status):
        """TempPollerThread 시그널 수신 → 배너 + 그룹 레이블 갱신 (메인 스레드)."""
        if setpoint is None:
            setpoint = self.spin_temp.value()
        parts = []
        parts.append(f"Reading: {float(reading):.1f}°C" if reading is not None else "Reading: —")
        if setpoint is not None:
            parts.append(f"SP: {float(setpoint):.1f}°C")
        if status is not None:
            parts.append(str(status))
        self.lbl_temp_live.setText(f"🌡 {float(reading):.1f}°C" if reading is not None else "🌡 —")
        self.lbl_temp_reading.setText("  |  ".join(parts))

    # ── 배경 관련 ─────────────────────────────────────────────────────

    def _capture_bg(self):
        if self._cam is None:
            self._log("❌ 카메라 미연결")
            return
        try:
            frame = np.asarray(self._cam.snap()).astype(np.float32)
            save_dir = Path(self.edit_save_dir.text().strip() or "acquisitions")
            save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bg_path = save_dir / f"background_{ts}.spe"
            save_spe(bg_path, [frame], exposure_ms=self.spin_exposure.value())
            self._bg_frames = frame[np.newaxis]   # (1, H, W)
            h, w = frame.shape
            self._lbl_bg_status.setText(f"{w}×{h}  [{bg_path.name}]")
            self._lbl_bg_status.setStyleSheet(
                f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FS_SMALL};"
            )
            self.check_bg_sub.setEnabled(True)
            self.check_bg_sub.setChecked(True)
            self._log(f"📸 BG 획득 저장: {bg_path}")
        except Exception as e:
            self._log(f"❌ BG 획득 실패: {e}")

    def _load_bg_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "BG SPE 파일 선택", "", "SPE Files (*.spe);;All Files (*)"
        )
        if not path:
            return
        try:
            frames = self._read_spe_frames(path)   # (N, H, W) float32
            self._bg_frames = frames
            n, h, w = frames.shape
            name = os.path.basename(path)
            self._lbl_bg_status.setText(f"{w}×{h}  {n}f  [{name}]")
            self._lbl_bg_status.setStyleSheet(
                f"color: #4ecdc4; font-family: '{_FC}'; font-size: {_FS_SMALL};"
            )
            self.check_bg_sub.setEnabled(True)
            self.check_bg_sub.setChecked(True)
            self._log(f"📂 BG 로드: {name}  ({n} 프레임, {w}×{h})")
        except Exception as e:
            self._log(f"❌ BG 파일 로드 실패: {e}")

    def _read_spe_frames(self, path: str) -> np.ndarray:
        """SpeAnalyze 호환 SPE 3.0 reader → (N, H, W) float32."""
        import struct as _s
        with open(path, "rb") as f:
            header = f.read(4100)
        width      = _s.unpack_from("<H", header, 42)[0]
        height     = _s.unpack_from("<H", header, 656)[0]
        dtype_code = _s.unpack_from("<h", header, 108)[0]
        nframes    = _s.unpack_from("<i", header, 1446)[0]
        _dmap = {0: np.float32, 1: np.int32, 2: np.int16, 3: np.uint16, 8: np.uint32}
        dtype = _dmap.get(dtype_code, np.uint16)
        frame_bytes = width * height * np.dtype(dtype).itemsize
        frames = []
        with open(path, "rb") as f:
            f.seek(4100)
            for _ in range(max(nframes, 1)):
                raw = f.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                frames.append(
                    np.frombuffer(raw, dtype=dtype).reshape(height, width).astype(np.float32)
                )
        if not frames:
            raise ValueError("SPE 파일에서 프레임을 읽을 수 없습니다")
        return np.stack(frames, axis=0)

    def _apply_bg(self, frame: np.ndarray) -> np.ndarray:
        """BG 차감 적용 (체크박스 활성 & BG 있을 때만)."""
        if not self.check_bg_sub.isChecked() or self._bg_frames is None:
            return frame
        bg = self._bg_frames.mean(axis=0)
        if bg.shape != frame.shape:
            return frame
        result = np.clip(frame.astype(np.float32) - bg, 0.0, None)
        return result.astype(np.float32)

    def _subtract_bg_from_list(self, frames: list) -> list:
        """프레임 리스트에 BG 차감 적용 (비활성 시 그대로 반환)."""
        if not self.check_bg_sub.isChecked() or self._bg_frames is None:
            return frames
        bg = self._bg_frames.mean(axis=0)
        result = []
        for f in frames:
            arr = np.asarray(f)
            if arr.shape == bg.shape:
                result.append(np.clip(arr.astype(np.float32) - bg, 0.0, None))
            else:
                result.append(arr)
        return result

    def _save_spe(self, frames: list) -> Path:
        save_dir = Path(self.edit_save_dir.text().strip() or "acquisitions")
        save_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"picam_{ts}_{len(frames)}frames.spe"
        out_path = save_dir / filename

        if self._cam is not None and isinstance(self._cam, PicamCamera):
            try:
                return self._cam.save_as_spe(
                    out_path, frames,
                    exposure_ms=self.spin_exposure.value(),
                )
            except Exception:
                pass

        return save_spe(
            out_path, frames,
            exposure_ms=self.spin_exposure.value(),
            camera_name=self._cam.camera_name() if self._cam else "Picam",
            software="SpeAnalyze-Acquisition",
        )

    # ── 유틸 ─────────────────────────────────────────────────────────

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "저장 디렉터리 선택")
        if d:
            self.edit_save_dir.setText(d)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_display.append(log_html(msg, ts))
        self.log_message.emit(msg)

    def cleanup(self):
        """카메라는 LiveTab 소유이므로 여기서 해제하지 않는다."""
        # #19 저장 경로 영속화
        QSettings("SpeAnalyze", "AcquisitionTab").setValue(
            "save_dir", self.edit_save_dir.text()
        )
        if self._temp_thread is not None:
            self._temp_thread.stop()
            self._temp_thread = None
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
