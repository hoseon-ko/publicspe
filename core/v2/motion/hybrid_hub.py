from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from core.v2.motion.engine import MotionEngineV2, MotionState, MotionResult
from core.logger import dev_logger

class HybridMotionHubV2(QObject):
    """
    V2 Hybrid Motion Hub.
    Orchestrates ACS, KIMM, and Picomotor engines.
    Focuses on Loose Coupling and Centralized Safety.
    """
    # Global Signals for UI (Loose Coupling)
    any_busy_changed = pyqtSignal(bool)
    global_state_changed = pyqtSignal(str) # Summary of all states
    emergency_occurred = pyqtSignal(str) # Error message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 1. Initialize Individual Engines
        self.acs = MotionEngineV2(name="ACS_6DOF")
        self.kimm = MotionEngineV2(name="KIMM_Z")
        self.pico = MotionEngineV2(name="PICO_MIRROR")
        
        self._engines = [self.acs, self.kimm, self.pico]
        self._any_busy = False
        
        # 2. Wire Internal Signals for Centralized Safety & State Tracking
        for eng in self._engines:
            eng.state_changed.connect(self._on_engine_state_changed)
            eng.finished.connect(self._on_engine_finished)
            
    # ── High-Level Accessors ──────────────────────────────────────────
    
    @property
    def is_any_busy(self) -> bool:
        return any(e.state != MotionState.IDLE for e in self._engines)

    def get_summary_state(self) -> str:
        """Returns a human-readable summary of all engine states."""
        states = [f"{e.name}:{e.state.name}" for e in self._engines]
        return " | ".join(states)

    # ── Centralized Safety Logic ──────────────────────────────────────
    
    @pyqtSlot(object)
    def _on_engine_state_changed(self, state: MotionState):
        """Monitor all engines. If any fault, stop everything."""
        if state == MotionState.FAULTED:
            msg = "EMERGENCY: Engine Fault Detected! Stopping all devices..."
            dev_logger.error(f"[HybridHub] {msg}")
            self.stop_all_immediate()
            self.emergency_occurred.emit(msg)
            
        # Update busy state for UI
        new_busy = self.is_any_busy
        if new_busy != self._any_busy:
            self._any_busy = new_busy
            self.any_busy_changed.emit(new_busy)
            
        self.global_state_changed.emit(self.get_summary_state())

    @pyqtSlot(object)
    def _on_engine_finished(self, result: MotionResult):
        if not result.success:
            dev_logger.warning(f"[HybridHub] Engine reported failure: {result.message}")
            # In V2, failed move often triggers FAULTED state, 
            # handled by _on_engine_state_changed.

    def stop_all_immediate(self):
        """Emergency Stop for all managed engines."""
        dev_logger.critical("[HybridHub] GLOBAL STOP REQUESTED")
        for eng in self._engines:
            eng.stop()
            
    def lock_all(self):
        """Ensure all engines are in IDLE (Safe/Locked) state."""
        for eng in self._engines:
            if eng.state != MotionState.IDLE:
                eng.stop()

    # ── Command Routing (Loose Coupling Facade) ───────────────────────

    def move_acs_6dof(self, targets: list[float], settling_ms: int = 300):
        return self.acs.move_to(targets, settling_ms)
        
    def move_kimm_z(self, target_um: float, settling_ms: int = 200):
        # KIMM is typically axis index 2 in our 6-axis mapping
        targets = [0.0] * 6
        targets[2] = target_um
        return self.kimm.move_to(targets, settling_ms)
        
    def move_pico(self, motor_idx: int, target_steps: int):
        # Pico uses 4 axes
        targets = [0.0] * 4
        targets[motor_idx] = float(target_steps)
        return self.pico.move_to(targets, settling_ms=0)
