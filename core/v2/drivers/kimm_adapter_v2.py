from core.v2.hal.motion_base import MotionHAL
from core.motor.kimm_z import KIMMZController

class KimmAdapterV2(MotionHAL):
    """
    V2 Adapter for KIMM Fine Stage.
    """
    def __init__(self, controller: KIMMZController):
        self._ctrl = controller
        
    def enable_all(self):
        # KIMM doesn't have a specific global enable command in the current driver
        # but we could implement servo-on check here if needed.
        pass
        
    def disable_all(self):
        pass
        
    def move_to(self, axis: int, position: float):
        # KIMM is Z-only for this adapter (Axis 3)
        # Note: position is in um for KIMM
        self._ctrl.move_to_z_async(position)
        
    def stop_all(self):
        # KIMM protocol for stop could be added here
        pass
        
    def is_enabled_all(self) -> bool:
        return True # Assume always enabled for now
        
    def is_enabled_any(self) -> bool:
        return True
        
    def is_moving_all(self) -> bool:
        return self._ctrl.is_moving
        
    def get_positions(self) -> list[float]:
        # Return [0, 0, Z, 0, 0, 0] or similar mapping
        z = self._ctrl.current_z
        return [0.0, 0.0, z, 0.0, 0.0, 0.0]
