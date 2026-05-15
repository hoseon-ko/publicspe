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
                try:
                    caps = self._session_hub.camera_get_capabilities()
                except Exception:
                    caps = None
                self._apply_camera_capabilities(caps)
                try:
                    ms = float(self._session_hub.camera_get_exposure_ms(OWNER_DEEPALIGN))
                    self.spin_exposure.blockSignals(True)
                    self.spin_exposure.setValue(ms)
                    self.spin_exposure.blockSignals(False)
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
                self._apply_camera_capabilities(None)
                self._set_camera_action_state(False)
                dev_logger.debug("[DeepAlign] disconnect via hub succeeded")
                return
            except Exception:
                dev_logger.exception("[DeepAlign] disconnect via hub failed")
                return
        dev_logger.debug("[DeepAlign] disconnect skipped (session hub is not bound)")
