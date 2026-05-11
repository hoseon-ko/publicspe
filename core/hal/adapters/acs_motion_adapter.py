"""ACS motion adapter implementing AcsHal."""

from __future__ import annotations

from core.hal.errors import HalCommandError, HalConnectionError, HalNotConnectedError
from core.hal.motion_hal import AcsHal
from core.logger import dev_logger
from core.motor.acs_stage import AcsStageController


class AcsMotionAdapter(AcsHal):
    def __init__(self):
        self._controller: AcsStageController | None = None

    def connect(self, ip: str, port: int) -> None:
        dev_logger.debug(f"[AcsMotionAdapter] connect requested ip={ip}, port={port}")
        try:
            ctrl = AcsStageController()
            ctrl.connect(ip, int(port))
            ctrl.start_polling()
            self._controller = ctrl
            dev_logger.debug("[AcsMotionAdapter] connect succeeded")
        except Exception as exc:
            dev_logger.exception(f"[AcsMotionAdapter] connect failed ip={ip}, port={port}")
            self._controller = None
            raise HalConnectionError(f"ACS connect failed: {exc}", cause=exc) from exc

    def connect_simulator(self) -> None:
        dev_logger.debug("[AcsMotionAdapter] connect_simulator requested")
        try:
            ctrl = AcsStageController()
            ctrl.connect_simulator()
            ctrl.start_polling()
            self._controller = ctrl
            dev_logger.debug("[AcsMotionAdapter] connect_simulator succeeded")
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] connect_simulator failed")
            self._controller = None
            raise HalConnectionError(f"ACS simulator connect failed: {exc}", cause=exc) from exc

    def disconnect(self) -> None:
        dev_logger.debug("[AcsMotionAdapter] disconnect requested")
        ctrl = self._controller
        if ctrl is None:
            dev_logger.debug("[AcsMotionAdapter] disconnect skipped (no controller)")
            return
        try:
            ctrl.disconnect()
            dev_logger.debug("[AcsMotionAdapter] disconnect succeeded")
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] disconnect failed")
            raise HalCommandError(f"ACS disconnect failed: {exc}", cause=exc) from exc
        finally:
            self._controller = None

    def enable_all(self) -> None:
        dev_logger.debug("[AcsMotionAdapter] enable_all requested")
        ctrl = self._require_connected()
        try:
            ctrl.enable_all()
            dev_logger.debug("[AcsMotionAdapter] enable_all succeeded")
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] enable_all failed")
            raise HalCommandError(f"ACS enable_all failed: {exc}", cause=exc) from exc

    def disable_all(self) -> None:
        dev_logger.debug("[AcsMotionAdapter] disable_all requested")
        ctrl = self._require_connected()
        try:
            ctrl.disable_all()
            dev_logger.debug("[AcsMotionAdapter] disable_all succeeded")
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] disable_all failed")
            raise HalCommandError(f"ACS disable_all failed: {exc}", cause=exc) from exc

    def stop_all(self) -> None:
        dev_logger.debug("[AcsMotionAdapter] stop_all requested")
        ctrl = self._require_connected()
        try:
            ctrl.stop_all()
            dev_logger.debug("[AcsMotionAdapter] stop_all succeeded")
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] stop_all failed")
            raise HalCommandError(f"ACS stop_all failed: {exc}", cause=exc) from exc

    def move_to(self, axis: int, pos_mm: float) -> None:
        dev_logger.debug(f"[AcsMotionAdapter] move_to requested axis={axis}, pos_mm={pos_mm}")
        ctrl = self._require_connected()
        try:
            ctrl.move_to(int(axis), float(pos_mm), wait=False)
            dev_logger.debug(f"[AcsMotionAdapter] move_to succeeded axis={axis}, pos_mm={pos_mm}")
        except Exception as exc:
            dev_logger.exception(f"[AcsMotionAdapter] move_to failed axis={axis}, pos_mm={pos_mm}")
            raise HalCommandError(f"ACS move_to failed: {exc}", cause=exc) from exc

    def get_positions(self) -> list[float]:
        dev_logger.debug("[AcsMotionAdapter] get_positions requested")
        ctrl = self._require_connected()
        try:
            positions = [float(ctrl.get_position(i)) for i in range(6)]
            dev_logger.debug(f"[AcsMotionAdapter] get_positions succeeded positions={positions}")
            return positions
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] get_positions failed")
            raise HalCommandError(f"ACS get_positions failed: {exc}", cause=exc) from exc

    def _require_connected(self) -> AcsStageController:
        if self._controller is None or not self._controller.is_connected:
            raise HalNotConnectedError("ACS controller is not connected")
        return self._controller
