from PyQt6.QtCore import QObject, pyqtSignal
from core.hal.errors import HalCommandError, HalConnectionError, HalNotConnectedError
from core.hal.motion_hal import AcsHal
from core.logger import dev_logger
from core.motor.acs_stage import AcsStageController


class AcsMotionAdapter(QObject):
    # Test comment
    positions_updated = pyqtSignal(list)
    state_updated = pyqtSignal(list)


    def __init__(self, parent=None):
        super().__init__(parent)
        self._controller: AcsStageController | None = None

    def connect(self, ip: str, port: int) -> None:
        dev_logger.debug(f"[AcsMotionAdapter] connect requested ip={ip}, port={port}")
        try:
            ctrl = AcsStageController()
            ctrl.connect(ip, int(port))
            
            # Connect legacy signals to adapter signals
            ctrl.positions_updated.connect(self.positions_updated)
            ctrl.states_updated.connect(self.state_updated)
            
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

    def move_atomic(self, targets: list[float]) -> None:
        dev_logger.debug(f"[AcsMotionAdapter] move_atomic requested targets={targets}")
        ctrl = self._require_connected()
        try:
            ctrl.move_atomic(targets)
            dev_logger.debug("[AcsMotionAdapter] move_atomic signal emitted")
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] move_atomic failed")
            raise HalCommandError(f"ACS move_atomic failed: {exc}", cause=exc) from exc

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

    def is_moving(self) -> bool:
        dev_logger.debug("[AcsMotionAdapter] is_moving requested")
        ctrl = self._require_connected()
        try:
            return any(ctrl.is_moving(i) for i in range(6))
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] is_moving failed")
            raise HalCommandError(f"ACS is_moving check failed: {exc}", cause=exc) from exc

    def wait_in_position_all(self, timeout_ms: int = 30000) -> None:
        dev_logger.debug(f"[AcsMotionAdapter] wait_in_position_all requested timeout_ms={timeout_ms}")
        ctrl = self._require_connected()
        try:
            ctrl.wait_in_position_all(timeout_ms)
            dev_logger.debug("[AcsMotionAdapter] wait_in_position_all finished")
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] wait_in_position_all failed")
            raise HalCommandError(f"ACS wait_in_position_all failed: {exc}", cause=exc) from exc

    def is_enabled_all(self) -> bool:
        dev_logger.debug("[AcsMotionAdapter] is_enabled_all requested")
        ctrl = self._require_connected()
        try:
            return all(ctrl.is_enabled(i) for i in range(6))
        except Exception as exc:
            dev_logger.exception("[AcsMotionAdapter] is_enabled_all failed")
            raise HalCommandError(f"ACS is_enabled_all failed: {exc}", cause=exc) from exc

    def _require_connected(self) -> AcsStageController:
        if self._controller is None or not self._controller.is_connected:
            raise HalNotConnectedError("ACS controller is not connected")
        return self._controller
