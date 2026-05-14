from __future__ import annotations
import time
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Callable, Any
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

class MotionState(Enum):
    IDLE = auto()
    ENABLING = auto()      # Requesting hardware unlock
    WAIT_ENABLE = auto()   # Waiting for hardware confirmation
    MOVING = auto()        # Physical movement in progress
    WAIT_INPOS = auto()    # Waiting for in-position bit
    SETTLING = auto()      # Vibrations damping time
    DISABLING = auto()     # Requesting hardware lock
    WAIT_DISABLE = auto()  # Waiting for lock confirmation
    FAULTED = auto()       # Error or interlock violation

@dataclass
class MotionResult:
    success: bool
    message: str
    error_code: int = 0

class MotionEngineV2(QObject):
    """
    Next-Gen Motion Orchestrator.
    Strict State Machine based execution for Atomic Move sequences.
    (Unlock -> Move -> Settle -> Lock)
    """
    state_changed = pyqtSignal(MotionState)
    step_progress = pyqtSignal(str, float) # step name, percentage
    finished = pyqtSignal(MotionResult)
    
    def __init__(self, name: str = "MotionEngine", parent=None):
        super().__init__(parent)
        self.name = name
        self._state = MotionState.IDLE
        self._timer = QTimer()
        self._timer.timeout.connect(self._on_tick)
        self._timer.setInterval(50) # 20Hz state processing
        
        # Internal control variables
        self._target_joints = None
        self._state_start_time = 0
        self._settling_time_ms = 300
        self._timeout_ms = 30000
        
        # Hardware abstraction hook
        self._hal = None

    @property
    def state(self) -> MotionState:
        return self._state

    @property
    def has_hal(self) -> bool:
        return self._hal is not None

    def set_hal(self, hal):
        self._hal = hal

    def move_to(self, joints: list[float], settling_ms: int = 300):
        """Starts the atomic move transaction."""
        if self._state != MotionState.IDLE:
            return MotionResult(False, f"Engine busy: {self._state.name}")
            
        self._target_joints = joints
        self._settling_time_ms = settling_ms
        self._transition(MotionState.ENABLING)
        self._timer.start()
        return MotionResult(True, "Sequence started")

    def stop(self):
        """Emergency stop and immediate lock request."""
        if self._hal:
            self._hal.stop_all()
        self._transition(MotionState.DISABLING)

    def _transition(self, new_state: MotionState):
        if self._state != new_state:
            self._state = new_state
            self._state_start_time = time.time()
            self.state_changed.emit(new_state)

    def _on_tick(self):
        if self._state == MotionState.IDLE:
            self._timer.stop()
            return

        try:
            self._handle_current_state()
        except Exception as e:
            self._handle_error(f"Execution Error: {e}")

    def _handle_current_state(self):
        now = time.time()
        elapsed_ms = (now - self._state_start_time) * 1000
        
        if self._state == MotionState.ENABLING:
            self.step_progress.emit("Unlocking Axes", 10)
            self._hal.enable_all()
            self._transition(MotionState.WAIT_ENABLE)

        elif self._state == MotionState.WAIT_ENABLE:
            if self._hal.is_enabled_all():
                self._transition(MotionState.MOVING)
            elif elapsed_ms > 2000:
                self._handle_error("Enable Timeout")

        elif self._state == MotionState.MOVING:
            self.step_progress.emit("Moving to Target", 30)
            for i, val in enumerate(self._target_joints):
                self._hal.move_to(i, val)
            self._transition(MotionState.WAIT_INPOS)

        elif self._state == MotionState.WAIT_INPOS:
            if not self._hal.is_moving_all():
                self._transition(MotionState.SETTLING)
            elif elapsed_ms > self._timeout_ms:
                self._handle_error("Motion Timeout")

        elif self._state == MotionState.SETTLING:
            progress = min(100, int((elapsed_ms / self._settling_time_ms) * 100))
            self.step_progress.emit("Settling", 70 + (progress * 0.2))
            if elapsed_ms >= self._settling_time_ms:
                self._transition(MotionState.DISABLING)

        elif self._state == MotionState.DISABLING:
            self.step_progress.emit("Locking Axes", 90)
            self._hal.disable_all()
            self._transition(MotionState.WAIT_DISABLE)

        elif self._state == MotionState.WAIT_DISABLE:
            if not self._hal.is_enabled_any():
                self._transition(MotionState.IDLE)
                self.finished.emit(MotionResult(True, "Success"))
            elif elapsed_ms > 2000:
                self._transition(MotionState.IDLE) # Force IDLE anyway
                self.finished.emit(MotionResult(True, "Locked (with warning)"))

    def _handle_error(self, msg: str):
        self._transition(MotionState.FAULTED)
        self.finished.emit(MotionResult(False, msg))
