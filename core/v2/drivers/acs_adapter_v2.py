from core.v2.hal.motion_base import MotionHAL
from core.motor.acs_stage import AcsStageController

class AcsAdapterV2(MotionHAL):
    """
    V2 Adapter for ACS Stage.
    Wraps existing controller to match the new MotionHAL interface.
    """
    def __init__(self, controller: AcsStageController):
        self._ctrl = controller
        
    def enable_all(self):
        self._ctrl.enable_all()
        
    def disable_all(self):
        self._ctrl.disable_all()
        
    def move_to(self, axis: int, position: float):
        self._ctrl.move_to(axis, position)
        
    def stop_all(self):
        self._ctrl.stop_all()
        
    def is_enabled_all(self) -> bool:
        return all(self._ctrl.is_enabled(i) for i in range(6))
        
    def is_enabled_any(self) -> bool:
        return any(self._ctrl.is_enabled(i) for i in range(6))
        
    def is_moving_all(self) -> bool:
        return any(self._ctrl.is_moving(i) for i in range(6))
        
    def get_positions(self) -> list[float]:
        return [self._ctrl.get_position(i) for i in range(6)]
