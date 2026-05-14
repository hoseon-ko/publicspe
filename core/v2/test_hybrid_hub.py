import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from core.v2.motion.hybrid_hub import HybridMotionHubV2
from core.v2.drivers.simulators import AcsSimulatorV2, KimmSimulatorV2, PicoSimulatorV2

def test_hybrid_hub():
    app = QApplication(sys.argv)
    
    # 1. Setup Hub with Simulators
    hub = HybridMotionHubV2()
    hub.acs.set_hal(AcsSimulatorV2())
    hub.kimm.set_hal(KimmSimulatorV2())
    hub.pico.set_hal(PicoSimulatorV2())
    
    # 2. Wire Global Signals
    hub.any_busy_changed.connect(lambda busy: print(f"[HUB-UI] Global Busy: {busy}"))
    hub.global_state_changed.connect(lambda state: print(f"[HUB-UI] States: {state}"))
    hub.emergency_occurred.connect(lambda msg: print(f"!!! {msg} !!!"))
    
    # 3. Test Simultaneous Command
    print("\n--- Testing Simultaneous Moves ---")
    hub.move_acs_6dof([10, 0, 0, 0, 0, 0])
    hub.move_kimm_z(250.0)
    
    # 4. Test Emergency Stop (simulate after 1 second)
    def trigger_emergency():
        print("\n--- Triggering Manual Global Stop ---")
        hub.stop_all_immediate()
        
    def check_finished():
        if not hub.is_any_busy:
            print("\n--- All Devices Stopped Safely ---")
            print(f"Final Summary: {hub.get_summary_state()}")
            app.quit()

    QTimer.singleShot(1500, trigger_emergency)
    
    # Monitor for completion
    monitor_timer = QTimer()
    monitor_timer.setInterval(100)
    monitor_timer.timeout.connect(check_finished)
    monitor_timer.start()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    test_hybrid_hub()
