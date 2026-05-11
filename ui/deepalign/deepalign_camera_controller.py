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

import time

from PyQt6.QtCore import QThread

from core.logger import dev_logger
from core.session.ownership import OWNER_DEEPALIGN
from ui.deepalign.deepalign_camera_hub_mixin import CameraHubMixin
from ui.deepalign.deepalign_timing import clamp_frame_elapsed, overall_progress_ratio, format_hms
from ui.deepalign.deepalign_workers import _AcquireWorker, _LiveWorker, _SnapWorker


class CameraControllerMixin(CameraHubMixin):
    def _is_hub_camera_connected(self) -> bool:
        if self._session_hub is None:
            return False
        try:
            state = self._session_hub.get_camera_state()
            return getattr(state, "connection", None) == "connected"
        except Exception:
            return False

    def _on_start_live_clicked(self):
        self._update_dash_label(self.btn_live_air, "LIVE", "ON AIR")
        self._update_dash_label(self.btn_acquire, "ACQUIRE", "")

        if self._session_hub is not None:
            if self._is_hub_camera_connected():
                self._start_hub_live()
            else:
                dev_logger.debug("[DeepAlign] live start ignored (hub camera disconnected)")
            return

        if self._live_tab is None:
            return
        self._live_tab._start_camera()

    def _on_stop_live_clicked(self):
        if self._acq_running:
            self._stop_acquire()
            return
        self._update_dash_label(self.btn_live_air, "LIVE", "")

        self._stop_hub_live()

        if self._live_tab is None:
            return
        self._live_tab._stop_camera()

    def _on_snap_clicked(self):
        if self._snap_in_progress:
            return

        if self._session_hub is not None:
            snap_fn = lambda: self._session_hub.snap(OWNER_DEEPALIGN)
        else:
            cam = getattr(self._live_tab, "_camera", None) if self._live_tab is not None else None
            if cam is None:
                return
            snap_fn = cam.snap

        self._snap_in_progress = True
        self._snap_expected_s = max(0.02, self._estimate_frame_seconds())
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
        ratio = min(0.95, elapsed / max(0.02, self._snap_expected_s))
        self._set_master_progress(int(ratio * 100.0))

    def _on_acquire_progress_tick(self) -> None:
        if not self._acq_running:
            self._acq_progress_timer.stop()
            return

        expected = max(0.001, float(self._acq_frame_expected_s))
        now_mono = time.monotonic()
        frame_elapsed = clamp_frame_elapsed(now_mono, self._acq_frame_started_at, expected)

        completed = max(0, min(self._acq_cur, self._acq_total))
        in_frame_ratio = min(1.0, frame_elapsed / expected)
        overall_ratio = overall_progress_ratio(completed, self._acq_total, in_frame_ratio)
        self._set_master_progress(int(overall_ratio * 100.0))
        self._update_acquire_times(skip_progress_calc=True)

    def _on_snap_success(self, raw) -> None:
        self._snap_in_progress = False
        self._snap_progress_timer.stop()
        self._set_master_progress(100)
        self._push_frame(raw)
        dev_logger.debug("[DeepAlign] snap completed")

    def _on_snap_error(self, msg: str) -> None:
        self._snap_in_progress = False
        self._snap_progress_timer.stop()
        self._set_master_progress(0)
        dev_logger.error(f"[DeepAlign] snap failed: {msg}")

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

        cam = getattr(self._live_tab, "_camera", None) if self._live_tab is not None else None
        if cam is None:
            return 0.05
        try:
            if hasattr(cam, "_get_frame_total_s"):
                return max(0.005, float(cam._get_frame_total_s()))
        except Exception:
            pass
        try:
            return max(0.005, float(cam.get_exposure_ms()) / 1000.0)
        except Exception:
            return 0.05

    def _on_acquire_clicked(self):
        if self._acq_running:
            return
        if self._session_hub is None and self._live_tab is None:
            return

        use_hub = self._session_hub is not None
        cam = None
        if not use_hub:
            cam = getattr(self._live_tab, "_camera", None)
            if cam is None:
                return

        is_live = False
        if hasattr(self._live_tab, "camera_panel"):
            is_live = self._live_tab.camera_panel.btn_stop.isEnabled()
        if self._live_worker_thread is not None and self._live_worker_thread.isRunning():
            is_live = True

        if is_live:
            try:
                self._on_stop_live_clicked()
            except Exception as exc:
                print(f"[DeepAlign] Failed to trigger physical stop before acquire: {exc}")

        frame_count = max(1, int(self.spin_frame_to_save.value()))

        self._acq_running = True
        self._acq_cur = 0
        self._acq_total = frame_count
        self._acq_stop_requested = False
        self._acq_started_at = time.monotonic()
        self._acq_frame_expected_s = self._estimate_frame_seconds()
        self._acq_avg_frame_s = 0.0
        self._acq_frame_started_at = self._acq_started_at
        self._acq_frame_idx = 1

        self._update_dash_label(self.btn_acquire, "ACQUIRE", "RECORDING")
        self._set_master_progress(0)
        self.lbl_frame_info.setText(f"FRAME: <font color='#f8fafc'>0 / {frame_count}</font>")
        self._update_acquire_times()

        self.btn_acquire.setEnabled(False)
        self.btn_live_air.setEnabled(False)
        self.btn_snap.setEnabled(False)
        self.btn_connect.setEnabled(False)
        self.btn_disconnect.setEnabled(False)

        self._acq_thread = QThread(self)
        if use_hub:
            self._acq_worker = _AcquireWorker(
                None,
                frame_count,
                acquire_fn=lambda n, on_frame, should_stop: self._session_hub.acquire_with_progress(
                    OWNER_DEEPALIGN,
                    n,
                    on_frame=on_frame,
                    should_stop=should_stop,
                ),
            )
        else:
            self._acq_worker = _AcquireWorker(cam, frame_count)
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
        if not self._acq_running:
            return
        self._acq_stop_requested = True
        self._update_dash_label(self.btn_acquire, "ACQUIRE", "STOPPING")
        self._update_dash_label(self.btn_live_air, "LIVE", "")
        if self._acq_worker is not None:
            self._acq_worker.stop()

    def _on_acquire_frame_started(self, idx: int, total: int):
        self._acq_frame_idx = int(idx)
        self._acq_total = max(1, int(total))
        self._acq_frame_started_at = time.monotonic()
        self._update_acquire_times(skip_progress_calc=True)

    def _on_acquire_progress(self, cur: int, total: int, raw):
        self._acq_cur = int(cur)
        self._acq_total = max(1, int(total))
        self._acq_frame_started_at = time.monotonic()

        elapsed = max(0.0, time.monotonic() - self._acq_started_at)
        if self._acq_cur > 0:
            self._acq_avg_frame_s = elapsed / float(self._acq_cur)

        self.lbl_frame_info.setText(
            f"FRAME: <font color='#f8fafc'>{self._acq_cur} / {self._acq_total}</font>"
        )
        self._update_acquire_times(skip_progress_calc=True)
        self._push_frame(raw)

    def _on_acquire_finished(self, frames: list):
        self._acq_running = False
        self._acq_progress_timer.stop()
        stopped = self._acq_stop_requested or (len(frames) < self._acq_total)
        if stopped:
            final_pct = int(100 * len(frames) / max(1, self._acq_total))
            self._set_master_progress(final_pct)
        else:
            self._set_master_progress(100 if self._acq_total > 0 else 0)
        final_total = self._acq_total if self._acq_total > 0 else len(frames)
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
            self._update_dash_label(self.btn_acquire, "ACQUIRE", "DONE")
        self._restore_after_acquire()

    def _on_acquire_error(self, msg: str):
        self._acq_running = False
        self._acq_progress_timer.stop()
        self._set_master_progress(0)
        self.lbl_times.setText(
            "FRAME TIME: <font color='#f8fafc'>ERROR</font> | REMAIN: "
            "<font color='#f8fafc'>00:00:00</font> | ETA: <font color='#f8fafc'>ERROR</font>"
        )
        print(f"[DeepAlign] Acquire failed: {msg}")
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
        connected = (self._camera is not None) or self._is_hub_camera_connected()
        self._set_camera_action_state(connected)
        self.btn_acquire.setEnabled(connected)

    def _start_hub_live(self) -> None:
        if self._live_worker_thread is not None:
            if self._live_worker_thread.isRunning():
                return
            self._cleanup_live_worker()

        try:
            exp_ms = max(10.0, float(self.spin_exposure.value()))
            self._hub_live_progress_cycle_s = max(0.02, exp_ms / 1000.0)
        except Exception:
            self._hub_live_progress_cycle_s = 0.05

        snap_fn = lambda: self._session_hub.snap(OWNER_DEEPALIGN)
        self._live_worker = _LiveWorker(snap_fn)
        self._live_worker_thread = QThread(self)
        self._live_worker.moveToThread(self._live_worker_thread)

        self._live_worker_thread.started.connect(self._live_worker.run)
        self._live_worker.frame_ready.connect(self._on_hub_live_frame)
        self._live_worker.error.connect(self._on_hub_live_error)
        self._live_worker_thread.finished.connect(self._cleanup_live_worker)

        self._hub_live_progress_started_at = time.monotonic()
        self._set_master_progress(0)
        self._hub_live_progress_timer.start()
        self._live_worker_thread.start()
        dev_logger.debug("[DeepAlign] hub live worker started")

    def _stop_hub_live(self) -> None:
        if self._live_worker is not None:
            self._live_worker.stop()
        if self._live_worker_thread is not None:
            self._live_worker_thread.quit()
            self._live_worker_thread.wait()
        if self._hub_live_progress_timer.isActive():
            self._hub_live_progress_timer.stop()
        self._set_master_progress(0)
        dev_logger.debug("[DeepAlign] hub live worker stopped")

    def _on_hub_live_frame(self, raw) -> None:
        if self._acq_running:
            return
        self._hub_live_progress_started_at = time.monotonic()
        self._push_frame(raw)

    def _on_hub_live_error(self, msg: str) -> None:
        dev_logger.exception(f"[DeepAlign] hub live worker error: {msg}")
        self._stop_hub_live()

    def _cleanup_live_worker(self) -> None:
        if self._live_worker is not None:
            self._live_worker.deleteLater()
            self._live_worker = None
        if self._live_worker_thread is not None:
            self._live_worker_thread.deleteLater()
            self._live_worker_thread = None

    def _on_hub_live_progress_tick(self) -> None:
        if not self._hub_live_progress_timer.isActive() or self._acq_running:
            return
        if self._live_worker_thread is None or not self._live_worker_thread.isRunning():
            return
        cycle = max(0.02, float(self._hub_live_progress_cycle_s))
        elapsed = max(0.0, time.monotonic() - self._hub_live_progress_started_at)
        ratio = min(1.0, elapsed / cycle)
        self._set_master_progress(int(ratio * 100.0))

    def closeEvent(self, event):
        try:
            self._stop_hub_live()
            if self._snap_thread is not None and self._snap_thread.isRunning():
                self._snap_worker.stop()
                self._snap_thread.quit()
                self._snap_thread.wait(1000)
        except Exception as exc:
            dev_logger.exception(f"[DeepAlign] closeEvent error: {exc}")
        super().closeEvent(event)

    @staticmethod
    def _fmt_hms(seconds: float) -> str:
        return format_hms(seconds)

    def _update_acquire_times(self, force_done: bool = False, skip_progress_calc: bool = False):
        if self._acq_started_at <= 0:
            self.lbl_times.setText(
                "FRAME TIME: <font color='#f8fafc'>0.00 / 0.00s</font> | REMAIN: "
                "<font color='#f8fafc'>00:00:00</font> | ETA: <font color='#f8fafc'>00:00:00</font>"
            )
            return

        now_mono = time.monotonic()
        expected = max(0.001, float(self._acq_frame_expected_s))
        effective_frame_s = expected
        if self._acq_avg_frame_s > 0:
            effective_frame_s = max(0.001, float(self._acq_avg_frame_s))
        frame_elapsed = 0.0 if force_done else clamp_frame_elapsed(now_mono, self._acq_frame_started_at, expected)

        completed = max(0, min(self._acq_cur, self._acq_total))
        in_frame_ratio = 0.0 if force_done else min(1.0, frame_elapsed / expected)

        if not skip_progress_calc:
            overall_ratio = overall_progress_ratio(completed, self._acq_total, in_frame_ratio)
            self._set_master_progress(int(overall_ratio * 100.0))

        remaining_frames = max(0.0, float(self._acq_total - completed) - in_frame_ratio)
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
        if self._live_tab is not None and hasattr(self._live_tab, "camera_panel"):
            panel = self._live_tab.camera_panel
            panel.spin_exposure.setValue(float(self.spin_exposure.value()))
            panel._apply_exposure()

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
        if self._camera is None:
            return
        try:
            if self.check_fps_lock.isChecked():
                self._camera.set_fps(float(self.spin_fps.value()))
        except Exception:
            pass

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
        if self._camera is None:
            return
        try:
            target = float(self.spin_temp.value())
            self._camera.set_temperature(target)
            reading, setpoint, status = self._camera.get_temperature()
            self.lbl_temp_read.setText(f"Reading: {reading}")
            self.lbl_temp_set.setText(f"Setpoint: {setpoint}")
            self.lbl_temp_state.setText(f"Status: {status}")
        except Exception:
            pass

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
        if self._camera is None:
            return
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
            self._camera.set_adc_settings(**kwargs)
        except Exception:
            pass
