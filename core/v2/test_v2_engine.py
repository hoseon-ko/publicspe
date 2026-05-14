import sys
import time
from PyQt6.QtWidgets import QApplication
from core.v2.motion.engine import MotionEngineV2, MotionState
from core.v2.drivers.simulators import AcsSimulatorV2

def test_v2_engine():
    app = QApplication(sys.argv)
    
    # 1. Setup Engine and Simulator
    engine = MotionEngineV2()
    sim = AcsSimulatorV2()
    engine.set_hal(sim)
    
    # 2. Monitor Signals
    def on_state_changed(state):
        print(f"[TEST] State -> {state.name}")
        
    def on_progress(step, progress):
        print(f"       ... {step}: {progress}%")
        
    def on_finished(result):
        print(f"[TEST] Finished: {result.message} (Success={result.success})")
        print(f"[TEST] Final Positions: {sim.get_positions()}")
        app.quit()
        
    engine.state_changed.connect(on_state_changed)
    engine.step_progress.connect(on_progress)
    engine.finished.connect(on_finished)
    
    # 3. Start Atomic Move
    target = [1.0, 2.0, 3.0, 0.5, 0.5, 0.5]
    print(f"[TEST] Starting move to {target}")
    engine.move_to(target, settling_ms=500)
    
    # 4. Run Loop
    sys.exit(app.exec())

if __name__ == "__main__":
    test_v2_engine()
