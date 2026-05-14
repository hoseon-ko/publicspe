from enum import Enum
import time
import numpy as np
from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal

from core.hal.motion_hal import AcsHal
from core.hal.errors import HalCommandError, HalNotConnectedError, HalTimeoutError
from core.motor.kinematic_calc import KinematicCalc
from core.logger import dev_logger

class MotionState(Enum):
    LOCKED = "LOCKED"
    ENABLING = "ENABLING"
    MOVING = "MOVING"
    SETTLING = "SETTLING"
    LOCKING = "LOCKING"
    FAULTED = "FAULTED"

class MotionHub(QObject):
    """
    Central hub for motion control.
    Coordinates Kinematic calculations and Atomic Move sequences with a robust state machine.
    """
    
    # Signals for UI synchronization
    cartesian_updated = pyqtSignal(list)  # [tx, ty, tz, rx, ry, rz]
    joint_updated = pyqtSignal(list)      # [j1, j2, j3, j4, j5, j6]
    state_changed = pyqtSignal(MotionState)
    move_started = pyqtSignal()
    move_finished = pyqtSignal(bool, str) # success, message

    def __init__(self, acs_hal: Optional[AcsHal] = None, parent=None):
        super().__init__(parent)
        self._acs_hal = acs_hal
        self._kinematics = KinematicCalc()
        
        # Current States
        self._current_joints = [0.0] * 6
        self._current_cartesian = [0.0] * 6
        self._state = MotionState.LOCKED

    def attach_acs(self, hal: AcsHal):
        self._acs_hal = hal
        # If the HAL provides signals (like AcsMotionAdapter might via its controller), 
        # we can connect them here. 
        # For now, we assume the Hub will be polled or triggered by the session hub.
        dev_logger.info("[MotionHub] ACS HAL attached")

    def _set_state(self, state: MotionState):
        if self._state != state:
            self._state = state
            dev_logger.info(f"[MotionHub] State: {state.value}")
            self.state_changed.emit(state)

    def move_to_cartesian(self, tx: float, ty: float, tz: float, rx_mrad: float, ry_mrad: float, rz_mrad: float):
        """UI 요청: Cartesian 좌표로 이동."""
        if self._state != MotionState.LOCKED:
            raise HalCommandError(f"Cannot move: Current state is {self._state.value}")

        dev_logger.info(f"[MotionHub] Move to Cartesian: T=({tx}, {ty}, {tz}), R=({rx_mrad}, {ry_mrad}, {rz_mrad})")
        
        # 1. Inverse Kinematics
        targets, _, ok, violations = self._kinematics.calculate(
            [tx, ty, tz], [rx_mrad, ry_mrad, rz_mrad]
        )
        
        if not ok:
            msg = f"Kinematic Interlock: {', '.join(violations)}"
            dev_logger.warning(f"[MotionHub] {msg}")
            self.move_finished.emit(False, msg)
            raise HalCommandError(msg)

        # 2. Execute Atomic Move
        self._execute_atomic_move(targets)

    def _execute_atomic_move(self, joint_targets: np.ndarray):
        """
        Delegates Atomic Sequence to the Worker State Machine.
        """
        if self._acs_hal is None:
            raise HalNotConnectedError("ACS HAL not attached")

        self.move_started.emit()
        try:
            # Delegate to Worker's State Machine
            self._acs_hal.move_atomic(joint_targets.tolist())
            # Note: UI state will be updated via sync signals/state changes from worker
            self.move_finished.emit(True, "Move sequence initiated")
        except Exception as e:
            dev_logger.exception("[MotionHub] Failed to initiate move sequence")
            self.move_finished.emit(False, f"Initiation failed: {str(e)}")

    def update_joint_positions(self, joints: list[float]):
        """외부(Adapter/Controller)에서 Joint 위치 업데이트를 주입."""
        self._current_joints = joints
        self.joint_updated.emit(list(joints))
        
        # Forward Kinematics Sync
        res = self._kinematics.calculate_forward(np.array(joints))
        if res is not None:
            cartesian = [
                res[3], res[4], res[5],
                res[0]*1000.0, res[1]*1000.0, res[2]*1000.0
            ]
            self._current_cartesian = cartesian
            self.cartesian_updated.emit(cartesian)

    def sync_positions(self):
        """현재 하드웨어 위치를 읽어 Cartesian 좌표로 변환하여 UI 업데이트."""
        if self._acs_hal is None:
            return

        try:
            # 1. Get Real Joint Positions
            joints = self._acs_hal.get_positions()
            self._current_joints = joints
            self.joint_updated.emit(list(joints))
            
            # 2. Forward Kinematics
            res = self._kinematics.calculate_forward(np.array(joints))
            if res is not None:
                # res: [Rx, Ry, Rz, Tx, Ty, Tz] (rad, mm)
                # UI Format: [Tx, Ty, Tz, Rx_mrad, Ry_mrad, Rz_mrad]
                cartesian = [
                    res[3], res[4], res[5],    # Tx, Ty, Tz
                    res[0]*1000.0, res[1]*1000.0, res[2]*1000.0 # Rx, Ry, Rz (mrad)
                ]
                self._current_cartesian = cartesian
                self.cartesian_updated.emit(cartesian)
                
        except Exception as e:
            dev_logger.error(f"[MotionHub] Failed to sync positions: {e}")

    @property
    def state(self) -> MotionState:
        return self._state

    @property
    def current_cartesian(self) -> list[float]:
        return self._current_cartesian

    @property
    def current_joints(self) -> list[float]:
        return self._current_joints
