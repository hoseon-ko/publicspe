import sys
import os

# Add the project root to sys.path
project_root = r"d:\01_Project_Work\03. AMMI\SpeAnalyze"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def check_imports():
    print("Checking imports...")
    try:
        from core.hal.camera_hal import CameraCapabilities, CameraHal
        print("✅ core.hal.camera_hal imported")
        
        # Test instantiation
        caps = CameraCapabilities()
        print(f"✅ CameraCapabilities instantiated: {caps}")
        
        from core.hal.adapters.picam_camera_adapter import PicamCameraAdapter
        print("✅ core.hal.adapters.picam_camera_adapter imported")
        
        # Mocking PicamCamera for instantiation test
        # We don't want to actually connect to hardware
        
        from ui.deepalign.deepalign_layout import LayoutBuilderMixin
        print("✅ ui.deepalign.deepalign_layout imported")
        
        from ui.deepalign.deepalign_camera_hub_mixin import CameraHubMixin
        print("✅ ui.deepalign.deepalign_camera_hub_mixin imported")
        
        from ui.deepalign.deepalign_camera_controller import CameraControllerMixin
        print("✅ ui.deepalign.deepalign_camera_controller imported")

        print("\nAll major imports successful.")
        return True
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = check_imports()
    if not success:
        sys.exit(1)
    print("\nSyntax check passed.")
