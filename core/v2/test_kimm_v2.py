import sys
import time
from PyQt6.QtWidgets import QApplication
from core.v2.motion.engine import MotionEngineV2, MotionState
from core.v2.drivers.simulators import KimmSimulatorV2

def test_kimm_v2():
    app = QApplication(sys.argv)
    
    engine = MotionEngineV2()
    sim = KimmSimulatorV2() # KIMM Simulator
    engine.set_hal(sim)
    
    engine.state_changed.connect(lambda s: print(f"[KIMM-TEST] State -> {s.name}"))
    engine.finished.connect(lambda r: (print(f"[KIMM-TEST] Result: {r.message}"), print(f"[KIMM-TEST] Pos: {sim.get_positions()[2]}"), app.quit()))
    
    target_z = 500.0 # 500um
    print(f"[KIMM-TEST] Moving Z to {target_z} um")
    engine.move_to([0, 0, target_z, 0, 0, 0], settling_ms=200)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    test_kimm_v2()
