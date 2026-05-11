"""KIMM motion adapter implementing KimmHal."""

from __future__ import annotations

from core.hal.errors import HalCommandError, HalConnectionError, HalNotConnectedError
from core.hal.motion_hal import KimmHal
from core.logger import dev_logger
from core.motor.kimm_z import KIMMZController


class KimmMotionAdapter(KimmHal):
    def __init__(self):
        self._controller: KIMMZController | None = None

    def connect(self, ip: str, port: int) -> None:
        dev_logger.debug(f"[KimmMotionAdapter] connect requested ip={ip}, port={port}")
        try:
            ctrl = KIMMZController(ip, int(port))
            ctrl.connect()
            self._controller = ctrl
            dev_logger.debug("[KimmMotionAdapter] connect succeeded")
        except Exception as exc:
            dev_logger.exception(f"[KimmMotionAdapter] connect failed ip={ip}, port={port}")
            self._controller = None
            raise HalConnectionError(f"KIMM connect failed: {exc}", cause=exc) from exc

    def disconnect(self) -> None:
        dev_logger.debug("[KimmMotionAdapter] disconnect requested")
        ctrl = self._controller
        if ctrl is None:
            dev_logger.debug("[KimmMotionAdapter] disconnect skipped (no controller)")
            return
        try:
            ctrl.disconnect()
            dev_logger.debug("[KimmMotionAdapter] disconnect succeeded")
        except Exception as exc:
            dev_logger.exception("[KimmMotionAdapter] disconnect failed")
            raise HalCommandError(f"KIMM disconnect failed: {exc}", cause=exc) from exc
        finally:
            self._controller = None

    def move_to_z(self, um: float) -> None:
        dev_logger.debug(f"[KimmMotionAdapter] move_to_z requested um={um}")
        ctrl = self._require_connected()
        try:
            ctrl.move_to_z(float(um))
            dev_logger.debug(f"[KimmMotionAdapter] move_to_z succeeded um={um}")
        except Exception as exc:
            dev_logger.exception(f"[KimmMotionAdapter] move_to_z failed um={um}")
            raise HalCommandError(f"KIMM move_to_z failed: {exc}", cause=exc) from exc

    def get_z(self) -> float:
        dev_logger.debug("[KimmMotionAdapter] get_z requested")
        ctrl = self._require_connected()
        try:
            ctrl.request_position()
            z = float(ctrl.current_z)
            dev_logger.debug(f"[KimmMotionAdapter] get_z succeeded z={z}")
            return z
        except Exception as exc:
            dev_logger.exception("[KimmMotionAdapter] get_z failed")
            raise HalCommandError(f"KIMM get_z failed: {exc}", cause=exc) from exc

    def _require_connected(self) -> KIMMZController:
        if self._controller is None or not self._controller.is_connected:
            raise HalNotConnectedError("KIMM controller is not connected")
        return self._controller
