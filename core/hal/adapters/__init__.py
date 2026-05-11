"""Thin adapter implementations for existing camera and motion controllers."""

from core.hal.adapters.acs_motion_adapter import AcsMotionAdapter
from core.hal.adapters.hikvision_camera_adapter import HikvisionCameraAdapter
from core.hal.adapters.kimm_motion_adapter import KimmMotionAdapter
from core.hal.adapters.picam_camera_adapter import PicamCameraAdapter
from core.hal.adapters.pico_motion_adapter import PicoMotionAdapter
from core.hal.adapters.simulated_camera_adapter import SimulatedCameraAdapter

__all__ = [
    "AcsMotionAdapter",
    "HikvisionCameraAdapter",
    "KimmMotionAdapter",
    "PicamCameraAdapter",
    "PicoMotionAdapter",
    "SimulatedCameraAdapter",
]
