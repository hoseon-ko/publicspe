import time
import numpy as np
from core.v2.hal.motion_base import MotionHAL

class BaseSimulatorV2(MotionHAL):
    """
    Base class for V2 Motion Simulators.
    Simulates linear movement over time.
    """
    def __init__(self, axis_count: int, speed: float = 10.0):
        self._axis_count = axis_count
        self._current_pos = [0.0] * axis_count
        self._target_pos = [0.0] * axis_count
        self._enabled = [False] * axis_count
        self._speed = speed # units per second
        self._last_tick = time.time()
        self._is_moving = False

    def enable_all(self):
        self._enabled = [True] * self._axis_count
        
    def disable_all(self):
        self._enabled = [False] * self._axis_count
        
    def move_to(self, axis: int, position: float):
        if not self._enabled[axis]: return
        self._target_pos[axis] = position
        self._is_moving = True
        self._last_tick = time.time()
        
    def stop_all(self):
        self._target_pos = list(self._current_pos)
        self._is_moving = False
        
    def is_enabled_all(self) -> bool:
        return all(self._enabled)
        
    def is_enabled_any(self) -> bool:
        return any(self._enabled)
        
    def is_moving_all(self) -> bool:
        self._update_logic()
        return self._is_moving
        
    def get_positions(self) -> list[float]:
        self._update_logic()
        return list(self._current_pos)

    def _update_logic(self):
        """Simulates physics/movement."""
        now = time.time()
        dt = now - self._last_tick
        self._last_tick = now
        
        if not self._is_moving: return
        
        still_moving = False
        for i in range(self._axis_count):
            diff = self._target_pos[i] - self._current_pos[i]
            if abs(diff) < 0.001:
                self._current_pos[i] = self._target_pos[i]
                continue
            
            step = self._speed * dt
            if abs(diff) <= step:
                self._current_pos[i] = self._target_pos[i]
            else:
                self._current_pos[i] += (step if diff > 0 else -step)
                still_moving = True
        
        self._is_moving = still_moving

class AcsSimulatorV2(BaseSimulatorV2):
    def __init__(self):
        super().__init__(axis_count=6, speed=20.0) # 20mm/s

class KimmSimulatorV2(BaseSimulatorV2):
    def __init__(self):
        super().__init__(axis_count=6, speed=100.0) # 100um/s
        # Note: KIMM is 1-axis but we use 6 for HAL compatibility

class PicoSimulatorV2(BaseSimulatorV2):
    def __init__(self):
        super().__init__(axis_count=4, speed=50.0) # 50 steps/s
