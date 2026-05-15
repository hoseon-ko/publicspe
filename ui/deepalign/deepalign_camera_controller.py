"""DeepAlign 카메라 제어 파일.

이 파일은 DeepAlign 탭의 실제 카메라 동작을 담당합니다.
주요 역할은 다음과 같습니다.
- live/snap/acquire 명령 처리
- 마스터 바에 표시되는 진행률 및 시간 갱신
- exposure/FPS/temperature/ADC 적용 처리
- hub 경로와 직접 카메라 경로의 실행 분기
- live/snap/acquire worker 정리
"""

from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import time

from PyQt6.QtCore import QThread, QTimer, pyqtSlot

from core.logger import dev_logger
from core.session.ownership import OWNER_DEEPALIGN
from core.session.session_state import CameraConnectionState
from core.spe_writer import save_spe
from ui.deepalign.deepalign_camera_hub_mixin import CameraHubMixin
from ui.deepalign.deepalign_timing import clamp_frame_elapsed, overall_progress_ratio, format_hms
from ui.deepalign.deepalign_workers import _AcquireWorker, _SnapWorker


class CameraControllerMixin(CameraHubMixin):
    def _is_hub_camera_connected(self) -> bool:
        if self._session_hub is None:
            return False
        try:
            state = self._session_hub.get_camera_state()
            return getattr(state, "connection", None) == CameraConnectionState.CONNECTED
        except Exception:
            return False

    def _on_start_live_clicked(self):
        if not self._is_hub_camera_connected():
            dev_logger.debug("[DeepAlign] live start ignored (hub camera disconnected)")
            return

        self._update_dash_label(self.btn_live_air, "LIVE", "ON AIR")
        self._update_dash_label(self.btn_acquire, "ACQUIRE", "")
        self._start_hub_live()

    def _on_stop_live_clicked(self):
        if self._acq.running:
            self._stop_acquire()
            return
        self._update_dash_label(self.btn_live_air, "LIVE", "")

        self._stop_hub_live()

    def _on_snap_clicked(self):
        if self._snap_in_progress:
            return

        if not self._is_hub_camera_connected():
            dev_logger.debug("[DeepAlign] snap ignored (hub camera disconnected)")
            return
            
        # Hikvision 등 일부 카메라 SDK는 Live 스트리밍 도중 별도 스레드에서
        # 프레임 버퍼를 당겨오면(segfault) 프로그램이 즉시 강제 종료됩니다.
        # 따라서 Snap 전에 Live를 안전하게 정지합니다.
        self._was_live_before_snap = getattr(self, "_is_live", False)
        if self._was_live_before_snap:
            self._stop_hub_live()

        snap_fn = lambda: self._session_hub.snap(OWNER_DEEPALIGN)

        self._snap_in_progress = True
        self._set_camera_action_state(True, busy=True)
        # adaptive 값이 있으면 유지, 없으면 HAL 초기값 사용
        # 매번 HAL로 덮으면 EMA 학습이 무의미해짐
        hal_s = max(0.05, self._estimate_frame_seconds())
        if self._snap_expected_s <= 0.05:
            self._snap_expected_s = hal_s
        else:
            # HAL 변화(노출 변경 등)를 40% 반영, 기존 학습값 60% 유지
            self._snap_expected_s = 0.6 * self._snap_expected_s + 0.4 * hal_s
        dev_logger.debug(
            f"[DeepAlign] snap start expected={self._snap_expected_s:.3f}s hal={hal_s:.3f}s"
        )
        self._snap_started_at = time.monotonic()
        self._set_master_progress(0)
        self._snap_progress_timer.start()

        self._snap_thread = QThread(self)
        self._snap_worker = _SnapWorker(snap_fn)
        self._snap_worker.moveToThread(self._snap_thread)
        self._snap_thread.started.connect(self._snap_worker.run)
        self._snap_worker.success.connect(self._on_snap_success)
        self._snap_worker.error.connect(self._on_snap_error)
        self._snap_worker.success.connect(lambda _: self._snap_thread.quit())
        self._snap_worker.error.connect(lambda _: self._snap_thread.quit())
        self._snap_thread.finished.connect(self._cleanup_snap_thread)
        self._snap_thread.start()

    def _on_snap_progress_tick(self) -> None:
        if not self._snap_in_progress:
            self._snap_progress_timer.stop()
            return
        elapsed = max(0.0, time.monotonic() - self._snap_started_at)
        expected = max(0.05, self._snap_expected_s)
        # exp 기반 단조증가: elapsed=expected 시 ~95%, 그 이후도 계속 증가, 99%에서 cap
        # 불연속/역주행 없음, 예상 초과 후에도 항상 가시적으로 올라감
        ratio = min(0.99, 1.0 - math.exp(-3.0 * elapsed / expected))
        self._set_master_progress(int(ratio * 100.0))

    def _on_acquire_progress_tick(self) -> None:
        if not self._acq.running:
            self._acq_progress_timer.stop()
            return

        # avg_frame_s가 쌓이면 실측값 우선, 없으면 초기 추정값
        expected = max(0.001, float(
            self._acq.avg_frame_s if self._acq.avg_frame_s > 0 else self._acq.frame_expected_s
        ))
        now_mono = time.monotonic()
        elapsed = max(0.0, now_mono - self._acq.frame_started_at)

        completed = max(0, min(self._acq.cur, self._acq.total))
        # exp 기반 단조증가 (snap과 동일)
        in_frame_ratio = min(0.99, 1.0 - math.exp(-3.0 * elapsed / expected))

        overall_ratio = overall_progress_ratio(completed, self._acq.total, in_frame_ratio)
        self._set_master_progress(int(overall_ratio * 100.0))
        self._update_acquire_times(skip_progress_calc=True)

    def _on_snap_success(self, raw) -> None:
        actual_s = time.monotonic() - self._snap_started_at
        # EMA로 실제 시간 학습: 다음 snap 클릭 시 HAL과 60/40으로 혼합됨
        self._snap_expected_s = 0.6 * self._snap_expected_s + 0.4 * max(0.05, actual_s)
        dev_logger.debug(
            f"[DeepAlign] snap done actual={actual_s:.3f}s adaptive_next={self._snap_expected_s:.3f}s"
        )
        self._snap_in_progress = False
        self._snap_progress_timer.stop()
        self._set_master_progress(100)
        self._set_camera_action_state(self._is_hub_camera_connected(), busy=False)
        self._push_frame(raw)

        ts = datetime.now().strftime("%H:%M:%S")
        self._add_to_gallery(raw, f"Snap_{ts}")

        dev_logger.debug(f"[DeepAlign] snap completed actual_s={actual_s:.3f}")
        
        if getattr(self, "_was_live_before_snap", False):
            self._start_hub_live()

    def _on_snap_error(self, msg: str) -> None:
        self._snap_in_progress = False
        self._snap_progress_timer.stop()
        self._set_master_progress(0)
        self._set_camera_action_state(self._is_hub_camera_connected(), busy=False)
        dev_logger.error(f"[DeepAlign] snap failed: {msg}")
        
        if getattr(self, "_was_live_before_snap", False):
            self._start_hub_live()

    def _cleanup_snap_thread(self) -> None:
        if self._snap_worker is not None:
            self._snap_worker.deleteLater()
            self._snap_worker = None
        if self._snap_thread is not None:
            self._snap_thread.deleteLater()
            self._snap_thread = None

    def _estimate_frame_seconds(self) -> float:
        if self._session_hub is not None and self._is_hub_camera_connected():
            try:
                return float(self._session_hub.camera_get_frame_total_s(OWNER_DEEPALIGN))
            except Exception:
                dev_logger.exception("[DeepAlign] failed to estimate frame time from hub")
                try:
                    ms = float(self._session_hub.camera_get_exposure_ms(OWNER_DEEPALIGN))
                    return max(0.005, ms / 1000.0)
                except Exception:
                    try:
                        return max(0.005, float(self.spin_exposure.value()) / 1000.0)
                    except Exception:
                        return 0.05

        try:
            return max(0.005, float(self.spin_exposure.value()) / 1000.0)
        except Exception:
            return 0.05

    def _on_acquire_clicked(self):
        if self._acq.running:
            return
        if not self._is_hub_camera_connected():
            dev_logger.debug("[DeepAlign] acquire ignored (hub camera disconnected)")
            return

        if getattr(self, "_hub_live_active", False):
            try:
                self._stop_hub_live(after_stop=self._begin_acquire_if_ready)
            except Exception as exc:
                dev_logger.exception(f"[DeepAlign] Failed to trigger physical stop before acquire: {exc}")
            return

        self._begin_acquire_if_ready()

    def _begin_acquire_if_ready(self):
        if self._acq.running:
            return
        if not self._is_hub_camera_connected():
            dev_logger.debug("[DeepAlign] acquire deferred but hub camera disconnected")
            return

        frame_count = max(1, int(self.spin_frame_to_save.value()))

        self._acq.start(frame_count, self._estimate_frame_seconds())

        self._update_dash_label(self.btn_acquire, "ACQUIRE", "SAVING")
        self._set_master_progress(0)
        self.lbl_frame_info.setText(f"FRAME: <font color='#f8fafc'>0 / {frame_count}</font>")
        self._update_acquire_times()
        self._set_camera_action_state(True, busy=True)

        self._acq_thread = QThread(self)
        self._acq_worker = _AcquireWorker(
            frame_count,
            acquire_fn=lambda n, on_frame, should_stop: self._session_hub.acquire_with_progress(
                OWNER_DEEPALIGN,
                n,
                on_frame=on_frame,
                should_stop=should_stop,
            ),
        )
        self._acq_worker.moveToThread(self._acq_thread)
        self._acq_thread.started.connect(self._acq_worker.run)
        self._acq_worker.frame_started.connect(self._on_acquire_frame_started)
        self._acq_worker.progress.connect(self._on_acquire_progress)
        self._acq_worker.finished.connect(self._on_acquire_finished)
        self._acq_worker.error.connect(self._on_acquire_error)
        self._acq_worker.finished.connect(lambda _: self._acq_thread.quit())
        self._acq_worker.error.connect(lambda _: self._acq_thread.quit())
        self._acq_thread.finished.connect(self._cleanup_acquire_thread)
        self._acq_progress_timer.start()
        self._acq_thread.start()

    def _stop_acquire(self):
        if not self._acq.running:
            return
        self._acq.stop_requested = True
        self._update_dash_label(self.btn_acquire, "ACQUIRE", "STOPPING")
        self._update_dash_label(self.btn_live_air, "LIVE", "")
        if self._acq_worker is not None:
            self._acq_worker.stop()

    def _on_acquire_frame_started(self, idx: int, total: int):
        self._acq.frame_idx = int(idx)
        self._acq.total = max(1, int(total))
        self._acq.frame_started_at = time.monotonic()
        self._update_acquire_times(skip_progress_calc=True)

    def _on_acquire_progress(self, cur: int, total: int, raw):
        self._acq.cur = int(cur)
        self._acq.total = max(1, int(total))
        self._acq.frame_started_at = time.monotonic()

        elapsed = max(0.0, time.monotonic() - self._acq.started_at)
        if self._acq.cur > 0:
            self._acq.avg_frame_s = elapsed / float(self._acq.cur)

        self.lbl_frame_info.setText(
            f"FRAME: <font color='#f8fafc'>{self._acq.cur} / {self._acq.total}</font>"
        )
        self._update_acquire_times(skip_progress_calc=True)
        self._push_frame(raw)

        if cur == total:
            ts = datetime.now().strftime("%H:%M:%S")
            self._add_to_gallery(raw, f"Acq_Last_{ts}")

    def _on_acquire_finished(self, frames: list):
        self._acq.running = False
        self._acq_progress_timer.stop()
        stopped = self._acq.stop_requested or (len(frames) < self._acq.total)
        if stopped:
            final_pct = int(100 * len(frames) / max(1, self._acq.total))
            self._set_master_progress(final_pct)
        else:
            self._set_master_progress(100 if self._acq.total > 0 else 0)
        final_total = self._acq.total if self._acq.total > 0 else len(frames)
        self.lbl_frame_info.setText(
            f"FRAME: <font color='#f8fafc'>{len(frames)} / {final_total}</font>"
        )
        if stopped:
            self.lbl_times.setText(
                "FRAME TIME: <font color='#f8fafc'>STOPPED</font> | REMAIN: "
                "<font color='#f8fafc'>00:00:00</font> | ETA: <font color='#f8fafc'>STOP</font>"
            )
            self._update_dash_label(self.btn_acquire, "ACQUIRE", "STOPPED")
        else:
            self._update_acquire_times(force_done=True)
            self._update_dash_label(self.btn_acquire, "ACQUIRE", "SAVED")
            try:
                path = self._save_acquire_spe(frames)
                dev_logger.debug(f"[DeepAlign] acquire saved: {path}")
                if hasattr(self, "spe_saved"):
                    self.spe_saved.emit(str(path))
            except Exception as exc:
                dev_logger.exception(f"[DeepAlign] acquire save failed: {exc}")
        self._restore_after_acquire()

    def _on_acquire_error(self, msg: str):
        self._acq.running = False
        self._acq_progress_timer.stop()
        self._set_master_progress(0)
        self.lbl_times.setText(
            "FRAME TIME: <font color='#f8fafc'>ERROR</font> | REMAIN: "
            "<font color='#f8fafc'>00:00:00</font> | ETA: <font color='#f8fafc'>ERROR</font>"
        )
        dev_logger.error(f"[DeepAlign] Acquire failed: {msg}")
        self._update_dash_label(self.btn_acquire, "ACQUIRE", "FAILED")
        self._restore_after_acquire()

    def _cleanup_acquire_thread(self):
        if self._acq_worker is not None:
            self._acq_worker.deleteLater()
            self._acq_worker = None
        if self._acq_thread is not None:
            self._acq_thread.deleteLater()
            self._acq_thread = None

    def _restore_after_acquire(self):
        connected = self._is_hub_camera_connected()
        self._set_camera_action_state(connected, busy=False)

    def _build_acquire_save_path(self) -> Path:
        folder = Path(self.edit_folder.text().strip() or "Live_Captures")
        folder.mkdir(parents=True, exist_ok=True)

        stem = self._build_filename_stem()
        path = folder / f"{stem}.spe"

        if self.check_inc_name.isChecked():
            counter = 2
            while path.exists():
                stem = self._build_filename_stem(counter=f"{counter:04d}")
                path = folder / f"{stem}.spe"
                counter += 1
        elif path.exists():
            base = self.edit_file_base.text().strip() or "Capture"
            now = datetime.now()
            path = folder / f"{base}_{now.strftime('%Y%m%d_%H%M%S')}.spe"

        return path

    def _save_acquire_spe(self, frames: list) -> Path:
        if not frames:
            raise ValueError("No frames to save")

        path = self._build_acquire_save_path()
        vendor = self.cb_vendor.currentText().strip() or "DeepAlign"
        camera_name = vendor
        camera_model = vendor
        camera_serial = ""

        if self._session_hub is not None:
            try:
                state = self._session_hub.get_camera_state()
                camera_name = getattr(state, "vendor", "") or camera_name
                camera_model = getattr(state, "device_id", "") or camera_model
            except Exception:
                pass

        return save_spe(
            path,
            frames,
            exposure_ms=float(self.spin_exposure.value()),
            camera_name=camera_name,
            camera_model=camera_model,
            camera_serial=camera_serial,
            creator="DeepAlign",
            software="SpeAnalyze-DeepAlign",
            extra_metadata={
                "DeepAlign": {
                    "Owner": "DeepAlign",
                    "Frames": len(frames),
                    "Vendor": vendor,
                }
            },
        )

    def _start_hub_live(self) -> None:
        if self._session_hub is None:
            return
        if getattr(self, "_hub_live_active", False):
            return

        try:
            self._hub_live_progress_cycle_s = max(0.02, self._estimate_frame_seconds())
        except Exception:
            self._hub_live_progress_cycle_s = 0.05

        try:
            self._session_hub.frame_ready.connect(self._on_hub_live_frame_ready)
        except Exception:
            pass

        try:
            self._session_hub.start_stream(OWNER_DEEPALIGN)
            self._hub_live_active = True
        except Exception as exc:
            try:
                self._session_hub.frame_ready.disconnect(self._on_hub_live_frame_ready)
            except Exception:
                pass
            dev_logger.exception(f"[DeepAlign] hub live start failed: {exc}")
            self._update_dash_label(self.btn_live_air, "LIVE", "")
            return

        self._set_camera_action_state(True, busy=True)
        self._hub_live_progress_started_at = time.monotonic()
        self._set_master_progress(0)
        self._hub_live_progress_timer.start()
        dev_logger.debug("[DeepAlign] hub live stream started")

    def _stop_hub_live(self, after_stop=None) -> None:
        if not getattr(self, "_hub_live_active", False):
            if callable(after_stop):
                after_stop()
            return
        self._hub_live_active = False
        try:
            if self._session_hub is not None:
                self._session_hub.stop_stream(OWNER_DEEPALIGN)
        except Exception as exc:
            dev_logger.exception(f"[DeepAlign] hub live stop failed: {exc}")
        try:
            if self._session_hub is not None:
                self._session_hub.frame_ready.disconnect(self._on_hub_live_frame_ready)
        except Exception:
            pass
        if self._hub_live_progress_timer.isActive():
            self._hub_live_progress_timer.stop()
        self._set_master_progress(0)
        self._set_camera_action_state(self._is_hub_camera_connected(), busy=False)
        dev_logger.debug("[DeepAlign] hub live stream stopped")
        if callable(after_stop):
            after_stop()

    def _on_hub_live_frame(self, raw) -> None:
        if self._acq.running:
            return
        now = time.monotonic()
        # 실제 프레임 간격으로 cycle_s adaptive 업데이트 (EMA α=0.2)
        if self._hub_live_progress_started_at > 0:
            actual_interval = now - self._hub_live_progress_started_at
            if 0.01 < actual_interval < 10.0:
                self._hub_live_progress_cycle_s = (
                    0.8 * self._hub_live_progress_cycle_s + 0.2 * actual_interval
                )
        self._hub_live_progress_started_at = now
        self._push_frame(raw)

    def _on_hub_live_frame_ready(self, rgb, raw) -> None:
        self._on_hub_live_frame(raw)

    def _on_hub_live_progress_tick(self) -> None:
        if not self._hub_live_progress_timer.isActive() or self._acq.running:
            return
        if not getattr(self, "_hub_live_active", False):
            return
        cycle = max(0.02, float(self._hub_live_progress_cycle_s))
        elapsed = max(0.0, time.monotonic() - self._hub_live_progress_started_at)
        # 예상 초과 시 서서히 99%에 접근 (snap/acquire와 동일 패턴)
        if elapsed <= cycle:
            ratio = elapsed / cycle
        else:
            ratio = 0.99 - (0.04 / (1.0 + (elapsed - cycle)))
        self._set_master_progress(int(min(0.99, ratio) * 100.0))

    def closeEvent(self, event):
        try:
            # 1) Live 스트림 먼저 중단
            self._stop_hub_live()

            # 2) Acquire 진행 중이면 stop 플래그 세팅
            if self._acq.running:
                self._acq.stop_requested = True
                if self._acq_worker is not None:
                    self._acq_worker.stop()

            # 3) 모든 워커 스레드 순서대로 종료
            _threads = [
                (getattr(self, "_snap_thread",        None), "snap"),
                (getattr(self, "_acq_thread",         None), "acquire"),
                (getattr(self, "_live_worker_thread", None), "live"),
            ]
            for thread, name in _threads:
                if thread is not None and thread.isRunning():
                    thread.quit()
                    if not thread.wait(1500):
                        dev_logger.warning(
                            f"[DeepAlign] closeEvent: '{name}' thread did not stop within 1.5s"
                        )
        except Exception as exc:
            dev_logger.exception(f"[DeepAlign] closeEvent error: {exc}")
        super().closeEvent(event)

    @staticmethod
    def _fmt_hms(seconds: float) -> str:
        return format_hms(seconds)

    def _update_acquire_times(self, force_done: bool = False, skip_progress_calc: bool = False):
        if self._acq.started_at <= 0:
            self.lbl_times.setText(
                "FRAME TIME: <font color='#f8fafc'>0.00 / 0.00s</font> | REMAIN: "
                "<font color='#f8fafc'>00:00:00</font> | ETA: <font color='#f8fafc'>00:00:00</font>"
            )
            return

        now_mono = time.monotonic()
        expected = max(0.001, float(self._acq.frame_expected_s))
        effective_frame_s = expected
        if self._acq.avg_frame_s > 0:
            effective_frame_s = max(0.001, float(self._acq.avg_frame_s))
        frame_elapsed = 0.0 if force_done else clamp_frame_elapsed(now_mono, self._acq.frame_started_at, expected)

        completed = max(0, min(self._acq.cur, self._acq.total))
        in_frame_ratio = 0.0 if force_done else min(1.0, frame_elapsed / expected)

        if not skip_progress_calc:
            overall_ratio = overall_progress_ratio(completed, self._acq.total, in_frame_ratio)
            self._set_master_progress(int(overall_ratio * 100.0))

        remaining_frames = max(0.0, float(self._acq.total - completed) - in_frame_ratio)
        remain = max(0.0, remaining_frames * effective_frame_s)
        eta = time.time() + remain
        eta_txt = time.strftime("%H:%M:%S", time.localtime(eta))

        self.lbl_times.setText(
            f"FRAME TIME: <font color='#f8fafc'>{frame_elapsed:.2f} / {effective_frame_s:.2f}s</font> | "
            f"REMAIN: <font color='#f8fafc'>{self._fmt_hms(remain)}</font> | ETA: <font color='#f8fafc'>{eta_txt}</font>"
        )

    def _on_live_exposure_applied(self, ms: float):
        self.spin_exposure.blockSignals(True)
        self.spin_exposure.setValue(float(ms))
        self.spin_exposure.blockSignals(False)

    def _on_apply_exposure_clicked(self):
        if self._session_hub is not None and self._is_hub_camera_connected():
            try:
                requested = float(self.spin_exposure.value())
                actual = float(self._session_hub.camera_set_exposure_ms(OWNER_DEEPALIGN, requested))
                self.spin_exposure.blockSignals(True)
                self.spin_exposure.setValue(actual)
                self.spin_exposure.blockSignals(False)
                dev_logger.debug(
                    f"[DeepAlign] exposure applied via hub requested={requested:.3f}, actual={actual:.3f}"
                )
                return
            except Exception:
                dev_logger.exception("[DeepAlign] exposure apply via hub failed")
                return
        dev_logger.debug("[DeepAlign] exposure apply ignored (hub camera disconnected)")

    def _on_apply_fps_clicked(self):
        if self._session_hub is not None and self._is_hub_camera_connected():
            try:
                if self.check_fps_lock.isChecked():
                    actual = float(self._session_hub.camera_set_fps(OWNER_DEEPALIGN, float(self.spin_fps.value())))
                    self.spin_fps.blockSignals(True)
                    self.spin_fps.setValue(actual)
                    self.spin_fps.blockSignals(False)
                else:
                    self._session_hub.camera_disable_fps_lock(OWNER_DEEPALIGN)
                return
            except Exception:
                dev_logger.exception("[DeepAlign] fps apply via hub failed")
                return
        dev_logger.debug("[DeepAlign] fps apply ignored (hub camera disconnected)")

    def _on_apply_temp_clicked(self):
        if self._session_hub is not None and self._is_hub_camera_connected():
            try:
                target = float(self.spin_temp.value())
                reading, setpoint, status = self._session_hub.camera_set_temperature(OWNER_DEEPALIGN, target)
                self.lbl_temp_read.setText(f"Reading: {reading}")
                self.lbl_temp_set.setText(f"Setpoint: {setpoint}")
                self.lbl_temp_state.setText(f"Status: {status}")
                return
            except Exception:
                dev_logger.exception("[DeepAlign] temperature apply via hub failed")
                return
        dev_logger.debug("[DeepAlign] temperature apply ignored (hub camera disconnected)")

    def _on_apply_adc_clicked(self):
        if self._session_hub is not None and self._is_hub_camera_connected():
            kwargs = {
                "adc_quality": self.cb_adc_quality.currentText(),
                "adc_speed": self.cb_adc_speed.currentText(),
                "adc_analog_gain": self.cb_adc_gain.currentText(),
                "bit_depth": self.cb_adc_bit.currentText(),
            }
            kwargs = {k: v for k, v in kwargs.items() if v}
            if not kwargs:
                return
            try:
                self._session_hub.camera_set_adc_settings(OWNER_DEEPALIGN, **kwargs)
                return
            except Exception:
                dev_logger.exception("[DeepAlign] adc apply via hub failed")
                return
        dev_logger.debug("[DeepAlign] adc apply ignored (hub camera disconnected)")

    # ── 온도 폴링 ──────────────────────────────────────────────────────

    def _start_temp_polling(self):
        if not hasattr(self, "_temp_timer"):
            self._temp_timer = QTimer(self)
            self._temp_timer.setInterval(3000)
            self._temp_timer.timeout.connect(self._poll_temperature)
        self._temp_timer.start()

    def _stop_temp_polling(self):
        if hasattr(self, "_temp_timer"):
            self._temp_timer.stop()
        self.lbl_temp_read.setText("Reading: —")
        self.lbl_temp_set.setText("Setpoint: —")
        self.lbl_temp_state.setText("Status: —")

    @pyqtSlot()
    def _poll_temperature(self):
        if self._session_hub is None or not self._is_hub_camera_connected():
            self._stop_temp_polling()
            return
        # snap/acquire 워커가 _camera_lock을 보유 중이면 건너뜀 (UI 블로킹 방지)
        if self._acq.running or self._snap_in_progress:
            return
        try:
            reading, setpoint, status = self._session_hub.camera_get_temperature(OWNER_DEEPALIGN)
            
            # None 처리를 위해 문자열 변환 시 fallback 적용
            r_str = f"{float(reading):.2f}" if reading is not None else "—"
            s_str = f"{float(setpoint):.2f}" if setpoint is not None else "—"
            st_str = str(status) if status is not None else "—"

            self.lbl_temp_read.setText(f"Reading: {r_str}")
            self.lbl_temp_set.setText(f"Setpoint: {s_str}")
            self.lbl_temp_state.setText(f"Status: {st_str}")
        except Exception:
            pass
