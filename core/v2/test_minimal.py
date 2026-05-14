import sys
from PyQt6.QtWidgets import QApplication
from core.v2.motion.hybrid_hub import HybridMotionHubV2

def test_minimal():
    print("Test Started")
    app = QApplication(sys.argv)
    hub = HybridMotionHubV2()
    print(f"Hub Created: {hub.get_summary_state()}")
    print("Test Success")
    app.quit()

if __name__ == "__main__":
    test_minimal()
