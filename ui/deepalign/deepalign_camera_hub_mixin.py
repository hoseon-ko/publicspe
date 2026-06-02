"""DeepAlign 카메라 hub 연동 보조 파일.

이 파일은 DeepAlign를 session hub 및 필요 시 LiveTab과 연결하는 연결 계층 로직을 담습니다.
주요 역할은 다음과 같습니다.
- vendor 이름 정규화
- 카메라 scan 결과를 목록에 반영
- hub를 통한 connect/disconnect 처리
- LiveTab fallback 경로에서 vendor/device 목록 상태 복사
"""

from __future__ import annotations

from core.logger import dev_logger
from core.session.ownership import OWNER_DEEPALIGN


class CameraHubMixin:
    def _vendor_key(self) -> str:
        vendor = self.cb_vendor.currentText().strip().lower()
        if vendor in ("simulation", "simulated"):
            return "simulated"
        return vendor

    def _populate_camera_list_from_devices(self, devices: list[object]) -> None:
        self.cam_list.clear()
        for dev in devices:
            display = getattr(dev, "display_name", "") or getattr(dev, "device_id", "")
            self.cam_list.addItem(str(display))
        if self.cam_list.count() > 0:
            self.cam_list.setCurrentRow(0)

    def _sync_vendor_to_live(self):
        """Deprecated: DeepAlign camera ownership is hub-only."""
        return

    def _copy_camera_list_from_live(self):
        """Deprecated: DeepAlign camera ownership is hub-only."""
        return

    def _on_scan_clicked(self):
        if self._session_hub is not None:
            try:
                key = self._vendor_key()
                self._session_hub.select_camera_vendor(key)
                self._scanned_devices = list(self._session_hub.scan_cameras())
                self._populate_camera_list_from_devices(self._scanned_devices)
                dev_logger.debug(f"[DeepAlign] scan via hub succeeded vendor={key}, count={len(self._scanned_devices)}")
                return
            except Exception:
                dev_logger.exception("[DeepAlign] scan via hub failed")
                return
        dev_logger.debug("[DeepAlign] scan skipped (session hub is not bound)")

    def _push_saved_camera_settings(self, caps, vendor_cfg: str) -> None:
        """저장된 카메라 설정(exposure/fps/temp/ADC) 을 카메라에 다시 적용.

        카메라는 펌웨어 기본값으로 부팅하므로 직전 세션에서 쓰던 값을 명시적으로
        push 해야 한다. 수동 연결 / 자동 연결 양쪽에서 동일하게 호출되도록 분리.
        readback 전에 호출 — push → readback 순서가 중요.

        실패는 개별 try 로 격리해 다른 항목 push 를 막지 않는다.
        """
        cfg = self._cfg
        device_id = getattr(self, "_active_device_id", "")
        
        try:
            saved_exp = cfg.get_camera_setting("exposure_ms", None, vendor=vendor_cfg, device_id=device_id)
            if saved_exp is not None:
                self._session_hub.camera_set_exposure_ms(OWNER_DEEPALIGN, float(saved_exp))
        except Exception:
            dev_logger.exception("[DeepAlign] push exposure failed")

        if caps and getattr(caps, "has_fps_control", False):
            try:
                saved_fps_lock = bool(cfg.get_camera_setting("fps_lock", False, vendor=vendor_cfg, device_id=device_id))
                if saved_fps_lock:
                    saved_fps = cfg.get_camera_setting("fps", None, vendor=vendor_cfg, device_id=device_id)
                    if saved_fps is not None:
                        self._session_hub.camera_set_fps(OWNER_DEEPALIGN, float(saved_fps))
                else:
                    self._session_hub.camera_disable_fps_lock(OWNER_DEEPALIGN)
            except Exception:
                dev_logger.exception("[DeepAlign] push fps failed")

        if caps and getattr(caps, "has_temperature", False):
            try:
                saved_temp = cfg.get_camera_setting("temp_c", None, vendor=vendor_cfg, device_id=device_id)
                if saved_temp is not None:
                    self._session_hub.camera_set_temperature(OWNER_DEEPALIGN, float(saved_temp))
            except Exception:
                dev_logger.exception("[DeepAlign] push temperature failed")

        if caps and getattr(caps, "has_adc", False):
            # config: adc.quality/speed/gain/bit → hub: adc_quality/adc_speed/adc_analog_gain/bit_depth
            adc_map = {
                "adc.quality": "adc_quality",
                "adc.speed":   "adc_speed",
                "adc.gain":    "adc_analog_gain",
                "adc.bit":     "bit_depth",
            }
            adc_kwargs = {}
            for cfg_key, hub_key in adc_map.items():
                v = cfg.get_camera_setting(cfg_key, "", vendor=vendor_cfg, device_id=device_id)
                if v:
                    adc_kwargs[hub_key] = v
            if adc_kwargs:
                try:
                    self._session_hub.camera_set_adc_settings(OWNER_DEEPALIGN, **adc_kwargs)
                except Exception:
                    dev_logger.exception("[DeepAlign] push adc failed")

    def _on_connect_clicked(self):
        if self._session_hub is not None:
            cached_vendor = getattr(self._scanned_devices[0], "vendor", None) if self._scanned_devices else None
            if not self._scanned_devices or cached_vendor != self._vendor_key():
                self._on_scan_clicked()

        if self._session_hub is not None and self._scanned_devices:
            idx = max(0, self.cam_list.currentRow())
            if idx >= len(self._scanned_devices):
                idx = 0
            try:
                dev = self._scanned_devices[idx]
                device_id = getattr(dev, "device_id", "")
                self._session_hub.connect_camera(str(device_id))
                self._active_device_id = str(device_id)  # 장치별 설정 저장을 위한 현재 ID 기록
                try:
                    caps = self._session_hub.camera_get_capabilities()
                except Exception:
                    caps = None
                self._apply_camera_capabilities(caps)

                # ── [PUSH] config 의 이전 설정 → 카메라 (read-back 직전) ──
                vendor_cfg = self.cb_vendor.currentText().strip()
                self._push_saved_camera_settings(caps, vendor_cfg)

                # ── [READ-BACK] 카메라가 실제 적용한 값 ← UI ──
                try:
                    ms = float(self._session_hub.camera_get_exposure_ms(OWNER_DEEPALIGN))
                    self.spin_exposure.blockSignals(True)
                    self.spin_exposure.setValue(ms)
                    self.spin_exposure.blockSignals(False)
                except Exception:
                    pass

                # [ADDED] 온도 및 ADC 설정 즉시 동기화
                if caps and caps.has_temperature:
                    try:
                        reading, setpoint, status = self._session_hub.camera_get_temperature(OWNER_DEEPALIGN)
                        self.lbl_temp_read.setText(f"Reading: {reading}")
                        self.lbl_temp_set.setText(f"Setpoint: {setpoint}")
                        self.lbl_temp_state.setText(f"Status: {status}")
                        if setpoint is not None:
                            self.spin_temp.blockSignals(True)
                            self.spin_temp.setValue(float(setpoint))
                            self.spin_temp.blockSignals(False)
                    except Exception:
                        pass
                
                if caps and caps.has_adc:
                    try:
                        settings = self._session_hub.camera_get_adc_settings(OWNER_DEEPALIGN)
                        # settings 맵의 값을 콤보박스에 반영
                        mapping = {
                            "adc_quality": self.cb_adc_quality,
                            "adc_speed": self.cb_adc_speed,
                            "adc_analog_gain": self.cb_adc_gain,
                            "bit_depth": self.cb_adc_bit,
                        }
                        for key, cb in mapping.items():
                            val = settings.get(key)
                            if val is not None:
                                idx = cb.findText(str(val))
                                if idx >= 0:
                                    cb.blockSignals(True)
                                    cb.setCurrentIndex(idx)
                                    cb.blockSignals(False)
                    except Exception:
                        pass

                self._set_camera_action_state(True)
                has_temp = bool(caps and getattr(caps, "has_temperature", False))
                if has_temp:
                    self._start_temp_polling()
                dev_logger.debug(f"[DeepAlign] connect via hub succeeded device_id={device_id}")
                return
            except Exception:
                dev_logger.exception("[DeepAlign] connect via hub failed")
                return
        if self._session_hub is not None:
            dev_logger.debug("[DeepAlign] connect via hub skipped (no scanned device)")
            return
        dev_logger.debug("[DeepAlign] connect skipped (session hub is not bound)")

    def _on_disconnect_clicked(self):
        self._stop_hub_live()
        self._stop_temp_polling()
        if self._session_hub is not None:
            try:
                self._session_hub.disconnect_camera(reason="deep_align user request")
                self._active_device_id = ""
                self._apply_camera_capabilities(None)
                self._set_camera_action_state(False)
                dev_logger.debug("[DeepAlign] disconnect via hub succeeded")
                return
            except Exception:
                dev_logger.exception("[DeepAlign] disconnect via hub failed")
                return
        dev_logger.debug("[DeepAlign] disconnect skipped (session hub is not bound)")
