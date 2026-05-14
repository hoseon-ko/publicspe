from core.v2.hal.motion_base import MotionHAL
from core.motor.picomotor import PicomotorController

class PicoAdapterV2(MotionHAL):
    """
    V2 Adapter for Newport Picomotor 8742.
    Translates absolute position requests to relative step moves.
    """
    def __init__(self, controller: PicomotorController):
        self._ctrl = controller
        # Internal cache of moving state (Pico might need polling for this)
        self._is_moving = False
        
    def enable_all(self):
        # Picomotors don't have a servo-enable like ACS
        pass
        
    def disable_all(self):
        pass
        
    def move_to(self, axis: int, target_steps: float):
        # motor index in controller is 1-based (1, 2, 3, 4)
        motor = int(axis) + 1
        current = self._ctrl.get_position(motor)
        if current is None: current = 0
        
        delta = int(target_steps) - current
        if delta != 0:
            self._ctrl.move_relative(motor, delta)
            # Since Picomotor.move_relative is fire-and-forget, 
            # we'd ideally need a way to check if it's done.
            # For now, we assume it's moving.
            self._is_moving = True
            
    def stop_all(self):
        self._ctrl.stop_all()
        self._is_moving = False
        
    def is_enabled_all(self) -> bool:
        return True
        
    def is_enabled_any(self) -> bool:
        return True
        
    def is_moving_all(self) -> bool:
        # Polling logic for Picomotor movement status 
        # would be implemented here. For now, simple flag.
        # Ideally, check if current positions are changing.
        return self._is_moving
        
    def get_positions(self) -> list[float]:
        # Returns [M1, M2, M3, M4]
        return [float(p or 0) for p in self._ctrl.get_all_positions()]
