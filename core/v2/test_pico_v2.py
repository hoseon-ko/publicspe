import sys
from PyQt6.QtWidgets import QApplication
from core.v2.motion.engine import MotionEngineV2
from core.v2.drivers.simulators import PicoSimulatorV2

def test_pico_v2():
    app = QApplication(sys.argv)
    
    engine = MotionEngineV2()
    sim = PicoSimulatorV2()
    engine.set_hal(sim)
    
    engine.state_changed.connect(lambda s: print(f"[PICO-TEST] State -> {s.name}"))
    engine.finished.connect(lambda r: (print(f"[PICO-TEST] Result: {r.message}"), print(f"[PICO-TEST] Pos: {sim.get_positions()}"), app.quit()))
    
    target = [1000, -500, 250, 0] # steps
    print(f"[PICO-TEST] Moving motors to {target} steps")
    engine.move_to(target, settling_ms=100)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    test_pico_v2()
