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
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
    QComboBox, QCheckBox, QLineEdit, QProgressBar,
    QTextEdit, QFileDialog, QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt, QThread, QObject, QSettings, pyqtSignal

from core.async_worker import TempPollerThread
from core.camera.base import BaseCamera
from core.camera.picamp import PicamCamera
from core.spe_writer import save_spe
from ui.image_viewer import ImageViewer   # #15 프리뷰

_GRP_STYLE = """
QGroupBox {
    border: 1px solid #0f3460; border-radius: 6px;
    margin-top: 10px; font-family: 'Courier New';
    font-size: 11px; color: #e94560;
    letter-spacing: 2px; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
"""
_LBL = "color: #606880; font-family: 'Courier New'; font-size: 11px;"
_SPIN = """
QDoubleSpinBox, QSpinBox {
    background: #080e1e; border: 1px solid #0f3460; color: #c0d0ff;
    border-radius: 3px; font-family: 'Courier New'; font-size: 11px; padding: 2px 4px;
}
"""
_BTN = """
QPushButton {
    background: #0d1e38; color: #4ecdc4; border: 1px solid #1a4060;
    border-radius: 4px; font-family: 'Courier New'; font-weight: bold; padding: 5px 10px;
}
QPushButton:hover { background: #1a3a60; }
QPushButton:disabled { color: #1a2840; background: #080e1e; }
"""


def _grp(title: str) -> QGroupBox:
    g = QGroupBox(title)
    g.setStyleSheet(_GRP_STYLE)
    return g


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam: Optional[BaseCamera] = None
        self._worker: Optional[_AcqWorker] = None
        self._thread: Optional[QThread] = None
        self._acq_start_time: float = 0.0                     # ETA 계산용
        self._temp_thread: Optional[TempPollerThread] = None  # 온도 폴링 (백그라운드)
        self._build_ui()

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
            "color: #e94560; font-family: 'Courier New'; font-size: 16px;"
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
            "color: #e94560; font-family: 'Courier New'; font-size: 11px;"
        )
        banner_row.addWidget(self.lbl_cam_status, 1)
        # #16 온도 실시간 표시
        self.lbl_temp_live = QLabel("🌡 —")
        self.lbl_temp_live.setStyleSheet(
            "color: #a0c8ff; font-family: 'Courier New'; font-size: 10px;"
        )
        self.lbl_temp_live.setVisible(False)
        banner_row.addWidget(self.lbl_temp_live)
        left.addWidget(self._cam_banner)

        # Picam 전용 경고 (HIKVISION 연결 시 표시)
        self.lbl_picam_warn = QLabel("⚠️  Acquisition은 Picam 전용입니다")
        self.lbl_picam_warn.setStyleSheet(
            "color: #ffe66d; font-family: 'Courier New'; font-size: 11px; padding: 2px 0;"
        )
        self.lbl_picam_warn.setVisible(False)
        left.addWidget(self.lbl_picam_warn)

        # 노출 / 프레임 수
        grp_acq = _grp("ACQUISITION")
        ga = QVBoxLayout(grp_acq)
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
        self.grp_temp = _grp("TEMPERATURE (선택)")
        gt = QVBoxLayout(self.grp_temp)
        self.check_temp = QCheckBox("온도 설정 활성화")
        self.check_temp.setStyleSheet(
            "QCheckBox { color: #8090a8; font-family: 'Courier New'; font-size: 11px; }"
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
            "QCheckBox { color: #8090a8; font-family: 'Courier New'; font-size: 11px; }"
        )
        self.lbl_temp_reading = QLabel("Reading: —")
        self.lbl_temp_reading.setStyleSheet(
            "color: #a0c8ff; font-family: 'Courier New'; font-size: 11px;"
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
        self.grp_adc = _grp("ADC (선택)")
        gadc = QVBoxLayout(self.grp_adc)
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
            cb.setStyleSheet("""
                QComboBox { background: #080e1e; border: 1px solid #0f3460; color: #c0d0ff;
                    border-radius: 3px; font-family: 'Courier New'; font-size: 11px; padding: 2px 4px; }
                QComboBox QAbstractItemView { background: #0f1729; color: #c0d0ff; }
            """)
            row.addWidget(lbl)
            row.addWidget(cb, 1)
            gadc.addLayout(row)
            self._adc_combos[key] = cb
        self.grp_adc.setVisible(False)
        left.addWidget(self.grp_adc)

        # 저장 경로
        grp_save = _grp("SAVE")
        gs = QVBoxLayout(grp_save)
        path_row = QHBoxLayout()
        lbl_dir = QLabel("Dir:")
        lbl_dir.setStyleSheet(_LBL)
        self.edit_save_dir = QLineEdit("acquisitions")
        self.edit_save_dir.setStyleSheet("""
            QLineEdit { background: #080e1e; border: 1px solid #0f3460; color: #c0d0ff;
                border-radius: 3px; font-family: 'Courier New'; font-size: 11px; padding: 2px 4px; }
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
            "QCheckBox { color: #8090a8; font-family: 'Courier New'; font-size: 11px; }"
        )
        gs.addWidget(self.check_auto_open)
        left.addWidget(grp_save)

        # 획득 시작 버튼
        self.btn_acquire = QPushButton("▶  START ACQUISITION")
        self.btn_acquire.setFixedHeight(48)
        self.btn_acquire.setEnabled(False)
        self.btn_acquire.setStyleSheet("""
            QPushButton { background: #0d2820; color: #4ecdc4; border: 1px solid #1a5040;
                border-radius: 4px; font-family: 'Courier New'; font-weight: bold; font-size: 13px; }
            QPushButton:hover { background: #1a4838; }
            QPushButton:disabled { color: #1a2840; background: #080e1e; }
        """)
        left.addWidget(self.btn_acquire)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { background: #080e1e; border: 1px solid #0f3460; border-radius: 3px;
                color: #4ecdc4; font-family: 'Courier New'; font-size: 11px; text-align: center; }
            QProgressBar::chunk { background: #e94560; border-radius: 2px; }
        """)
        left.addWidget(self.progress_bar)

        # #7 ETA 레이블 (진행 중 남은 시간 표시)
        self.lbl_eta = QLabel("")
        self.lbl_eta.setVisible(False)
        self.lbl_eta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_eta.setStyleSheet(
            "color: #ffe66d; font-family: 'Courier New'; font-size: 11px;"
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
            "color: #e94560; font-family: 'Courier New'; font-size: 11px;"
            "font-weight: bold; letter-spacing: 2px;"
        )
        right_layout.addWidget(lbl_prev)
        self.preview_viewer = ImageViewer()
        self.preview_viewer.setMinimumHeight(220)
        right_layout.addWidget(self.preview_viewer, 2)

        # 로그
        lbl_log = QLabel("ACQUISITION LOG")
        lbl_log.setStyleSheet(
            "color: #e94560; font-family: 'Courier New'; font-size: 11px;"
            "font-weight: bold; letter-spacing: 2px;"
        )
        right_layout.addWidget(lbl_log)
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("""
            QTextEdit { background: #080e1e; border: 1px solid #0f3460;
                color: #00cc88; font-family: 'Courier New'; font-size: 11px; }
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
            "color: #4ecdc4; font-family: 'Courier New'; font-size: 11px;"
        )
        self.lbl_picam_warn.setVisible(not is_picam)
        self.btn_acquire.setEnabled(is_picam)

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
            "color: #e94560; font-family: 'Courier New'; font-size: 11px;"
        )
        self.lbl_picam_warn.setVisible(False)
        self.btn_acquire.setEnabled(False)
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

        # #7 예상 시간 계산
        total_s = n_frames * exposure_ms / 1000.0
        if total_s < 60:
            eta_str = f"{total_s:.1f}초"
        elif total_s < 3600:
            eta_str = f"{total_s/60:.1f}분 ({total_s:.0f}초)"
        else:
            eta_str = f"{total_s/3600:.2f}시간"

        self.btn_acquire.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, n_frames)
        self.progress_bar.setValue(0)
        self.lbl_eta.setText(f"⏱ 예상: {eta_str}  |  0/{n_frames} 완료")
        self.lbl_eta.setVisible(True)
        self._acq_start_time = time.monotonic()   # #7
        self._log(
            f"▶ 획득 시작: {n_frames} 프레임, "
            f"노출 {exposure_ms:.3f} ms"
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
        self.progress_bar.setValue(cur)

        # #7 실측 경과 시간 기반 ETA 계산
        elapsed = time.monotonic() - self._acq_start_time
        if cur > 0:
            rate = elapsed / cur          # 초/프레임
            remaining = rate * (total - cur)
            if remaining < 60:
                rem_str = f"{remaining:.0f}초 남음"
            else:
                rem_str = f"{remaining/60:.1f}분 남음"
            self.lbl_eta.setText(
                f"⏱ {cur}/{total} 완료  |  {rem_str}  ({elapsed:.1f}s 경과)"
            )
        else:
            self.lbl_eta.setText(f"⏱ 0/{total} 완료  |  —")
        self._log(f"  Frame {cur}/{total}")

    def _on_acquired(self, frames: list):
        self._thread.quit()   # run() 완료 후 자연 종료 — wait() 블로킹 불필요
        self.progress_bar.setVisible(False)
        self.btn_acquire.setEnabled(self._cam is not None and isinstance(self._cam, PicamCamera))

        # #7 총 소요 시간 표시
        elapsed = time.monotonic() - self._acq_start_time
        self.lbl_eta.setText(f"✅ 완료  |  총 소요: {elapsed:.1f}초")

        if not frames:
            self._log("❌ 프레임 획득 실패 (빈 결과)")
            return

        self._log(f"✅ {len(frames)} 프레임 획득 완료 (총 {elapsed:.1f}초) → 저장 중...")
        try:
            path = self._save_spe(frames)
            self._log(f"💾 저장: {path}")
            if self.check_auto_open.isChecked():
                self.spe_saved.emit(str(path))
        except Exception as e:
            self._log(f"❌ 저장 오류: {e}")

    def _on_acq_error(self, msg: str):
        self._thread.quit()
        self.progress_bar.setVisible(False)
        elapsed = time.monotonic() - self._acq_start_time
        self.lbl_eta.setText(f"❌ 오류  |  {elapsed:.1f}초 후 중단")
        self.btn_acquire.setEnabled(self._cam is not None and isinstance(self._cam, PicamCamera))
        self._log(f"❌ 획득 오류: {msg}")

    # ── #15 프리뷰 ────────────────────────────────────────────────────

    def _on_preview_frame(self, frame: np.ndarray):
        """워커 스레드에서 각 프레임 수신 → 프리뷰 갱신."""
        self.preview_viewer.set_image(frame)

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
        if any(k in msg for k in ("✅", "💾", "▶", "📸")):
            color = "#4ecdc4"
        elif "⚠️" in msg:
            color = "#ffe66d"
        elif any(k in msg for k in ("❌", "FAIL", "실패", "오류")):
            color = "#e94560"
        elif "⏱" in msg:
            color = "#ffe66d"
        elif any(k in msg for k in ("■", "해제")):
            color = "#4a5a7a"
        else:
            color = "#00cc88"
        ts_html = f"<span style='color:#2a4060;font-size:10px'>[{ts}]</span>"
        self.log_display.append(
            f"{ts_html} <span style='color:{color}'>{msg}</span>"
        )
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
