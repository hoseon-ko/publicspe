"""Picomotor motion adapter implementing PicoHal."""

from __future__ import annotations

from core.hal.errors import HalCommandError, HalConnectionError, HalNotConnectedError
from core.hal.motion_hal import PicoHal
from core.logger import dev_logger
from core.motor.picomotor import PicomotorController


class PicoMotionAdapter(PicoHal):
    def __init__(self):
        self._controller: PicomotorController | None = None

    def connect(self, *args, **kwargs) -> None:
        dev_logger.debug("[PicoMotionAdapter] connect requested")
        try:
            ctrl = PicomotorController()
            ctrl.connect()
            self._controller = ctrl
            dev_logger.debug("[PicoMotionAdapter] connect succeeded")
        except Exception as exc:
            dev_logger.exception("[PicoMotionAdapter] connect failed")
            self._controller = None
            raise HalConnectionError(f"Picomotor connect failed: {exc}", cause=exc) from exc

    def disconnect(self) -> None:
        dev_logger.debug("[PicoMotionAdapter] disconnect requested")
        ctrl = self._controller
        if ctrl is None:
            dev_logger.debug("[PicoMotionAdapter] disconnect skipped (no controller)")
            return
        try:
            ctrl.disconnect()
            dev_logger.debug("[PicoMotionAdapter] disconnect succeeded")
        except Exception as exc:
            dev_logger.exception("[PicoMotionAdapter] disconnect failed")
            raise HalCommandError(f"Picomotor disconnect failed: {exc}", cause=exc) from exc
        finally:
            self._controller = None

    def move_relative(self, axis: int, steps: int) -> None:
        dev_logger.debug(f"[PicoMotionAdapter] move_relative requested axis={axis}, steps={steps}")
        ctrl = self._require_connected()
        motor = self._to_motor_index(axis)
        try:
            ok = ctrl.move_relative(motor, int(steps))
            if not ok:
                raise HalCommandError(f"Picomotor move failed: axis={axis}, steps={steps}")
            dev_logger.debug(f"[PicoMotionAdapter] move_relative succeeded axis={axis}, steps={steps}")
        except HalCommandError:
            dev_logger.error(f"[PicoMotionAdapter] move_relative rejected axis={axis}, steps={steps}")
            raise
        except Exception as exc:
            dev_logger.exception(f"[PicoMotionAdapter] move_relative failed axis={axis}, steps={steps}")
            raise HalCommandError(f"Picomotor move_relative failed: {exc}", cause=exc) from exc

    def get_position(self, axis: int) -> int:
        dev_logger.debug(f"[PicoMotionAdapter] get_position requested axis={axis}")
        ctrl = self._require_connected()
        motor = self._to_motor_index(axis)
        try:
            pos = ctrl.get_position(motor)
            if pos is None:
                raise HalCommandError(f"Picomotor get_position returned None: axis={axis}")
            dev_logger.debug(f"[PicoMotionAdapter] get_position succeeded axis={axis}, pos={int(pos)}")
            return int(pos)
        except HalCommandError:
            dev_logger.error(f"[PicoMotionAdapter] get_position rejected axis={axis}")
            raise
        except Exception as exc:
            dev_logger.exception(f"[PicoMotionAdapter] get_position failed axis={axis}")
            raise HalCommandError(f"Picomotor get_position failed: {exc}", cause=exc) from exc

    def get_all_positions(self) -> list[int]:
        ctrl = self._require_connected()
        try:
            return [int(p) for p in ctrl.get_all_positions()]
        except Exception as exc:
            dev_logger.exception("[PicoMotionAdapter] get_all_positions failed")
            raise HalCommandError(f"Picomotor get_all_positions failed: {exc}", cause=exc) from exc

    def zero(self, axis: int) -> None:
        dev_logger.debug(f"[PicoMotionAdapter] zero requested axis={axis}")
        ctrl = self._require_connected()
        motor = self._to_motor_index(axis)
        try:
            ctrl.zero(motor)
            dev_logger.debug(f"[PicoMotionAdapter] zero succeeded axis={axis}")
        except Exception as exc:
            dev_logger.exception(f"[PicoMotionAdapter] zero failed axis={axis}")
            raise HalCommandError(f"Picomotor zero failed: {exc}", cause=exc) from exc

    def stop_all(self) -> None:
        dev_logger.debug("[PicoMotionAdapter] stop_all requested")
        ctrl = self._require_connected()
        try:
            ctrl.stop_all()
            dev_logger.debug("[PicoMotionAdapter] stop_all succeeded")
        except Exception as exc:
            dev_logger.exception("[PicoMotionAdapter] stop_all failed")
            raise HalCommandError(f"Picomotor stop_all failed: {exc}", cause=exc) from exc

    def _require_connected(self) -> PicomotorController:
        if self._controller is None or not self._controller.is_connected:
            raise HalNotConnectedError("Picomotor is not connected")
        return self._controller

    @staticmethod
    def _to_motor_index(axis: int) -> int:
        idx = int(axis)
        # PicomotorController expects 1-based motor index (1..4).
        # Reject ambiguous indexing to avoid moving the wrong motor.
        if 1 <= idx <= 4:
            return idx
        raise ValueError(f"Unsupported picomotor axis index: {axis}")
