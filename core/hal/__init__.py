"""Hardware abstraction layer interfaces."""

from core.hal.adapters import (
    AcsMotionAdapter,
    HikvisionCameraAdapter,
    KimmMotionAdapter,
    PicamCameraAdapter,
    PicoMotionAdapter,
    SimulatedCameraAdapter,
)
from core.hal.camera_hal import CameraCapabilities, CameraDeviceInfo, CameraHal
from core.hal.errors import (
    HalBusyError,
    HalCommandError,
    HalConnectionError,
    HalError,
    HalNotConnectedError,
    HalTimeoutError,
)
from core.hal.motion_hal import AcsHal, KimmHal, PicoHal

__all__ = [
    "AcsMotionAdapter",
    "AcsHal",
    "CameraCapabilities",
    "CameraDeviceInfo",
    "CameraHal",
    "HalBusyError",
    "HalCommandError",
    "HalConnectionError",
    "HalError",
    "HalNotConnectedError",
    "HalTimeoutError",
    "HikvisionCameraAdapter",
    "KimmHal",
    "KimmMotionAdapter",
    "PicamCameraAdapter",
    "PicoMotionAdapter",
    "PicoHal",
    "SimulatedCameraAdapter",
]
