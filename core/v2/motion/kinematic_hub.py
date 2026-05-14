from __future__ import annotations
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from core.v2.motion.engine import MotionEngineV2, MotionState, MotionResult
from core.motor.kinematic_calc import KinematicCalc
from core.logger import dev_logger

class KinematicMotionHubV2(QObject):
    """
    V2 High-level Motion Hub.
    Bridges Cartesian UI commands to Joint-space Engine execution.
    """
    cartesian_updated = pyqtSignal(list) # [x, y, z, rx, ry, rz]
    
    def __init__(self, engine: MotionEngineV2, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._kinematics = KinematicCalc()
        
        # Connect engine signals to propagate them if needed
        self.state_changed = self._engine.state_changed
        self.finished = self._engine.finished
        
    def move_to_cartesian(self, tx: float, ty: float, tz: float, rx_mrad: float, ry_mrad: float, rz_mrad: float):
        """
        Main entry point for UI Cartesian commands.
        Performs IK and starts the Engine sequence.
        """
        dev_logger.info(f"[HubV2] Cartesian Move Request: T=({tx}, {ty}, {tz}), R=({rx_mrad}, {ry_mrad}, {rz_mrad})")
        
        # 1. Inverse Kinematics
        targets, _, ok, violations = self._kinematics.calculate(
            [tx, ty, tz], [rx_mrad, ry_mrad, rz_mrad]
        )
        
        if not ok:
            msg = f"Kinematic Interlock: {', '.join(violations)}"
            dev_logger.warning(f"[HubV2] {msg}")
            return MotionResult(False, msg)

        # 2. Delegate to State-Machine Engine
        # Engine handles Unlock -> Move -> Settle -> Lock
        return self._engine.move_to(targets.tolist())

    def sync_cartesian(self, joints: list[float]):
        """
        Converts real joints back to Cartesian for UI display.
        """
        res = self._kinematics.calculate_forward(np.array(joints))
        if res is not None:
            # res: [Rx, Ry, Rz, Tx, Ty, Tz] (rad, mm)
            cartesian = [
                res[3], res[4], res[5],    # Tx, Ty, Tz
                res[0]*1000.0, res[1]*1000.0, res[2]*1000.0 # Rx, Ry, Rz (mrad)
            ]
            self.cartesian_updated.emit(cartesian)
            return cartesian
        return None
