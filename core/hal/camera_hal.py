"""Camera HAL protocol definitions."""

from __future__ import annotations

"""Camera HAL protocol definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

import numpy as np


@dataclass(slots=True)
class CameraCapabilities:
    has_exposure: bool = True
    has_live: bool = True
    has_temperature: bool = False
    has_adc: bool = False
    has_fps_control: bool = False
    has_binarize: bool = False
    supports_range_control: bool = True
    
    # Picam / Hardware specific
    temperature_range_c: tuple[float | None, float | None] = (None, None)
    adc_quality_options: list[str] = field(default_factory=list)
    adc_speed_options: list[str] = field(default_factory=list)
    adc_gain_options: list[str] = field(default_factory=list)
    adc_bit_depth_options: list[str] = field(default_factory=list)
    adc_port_options: list[str] = field(default_factory=list)
    
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CameraDeviceInfo:
    vendor: str
    device_id: str
    display_name: str
    serial: str = ""
    transport: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class CameraHal(Protocol):
    def capabilities(self) -> CameraCapabilities: ...

    def list_devices(self, vendor: str) -> list[CameraDeviceInfo]: ...

    def connect(self, device_id: str) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def set_exposure_ms(self, ms: float) -> None: ...

    def get_exposure_ms(self) -> float: ...

    def get_frame_total_s(self) -> float:
        """프레임 한 장 취득에 필요한 총 시간(초): EXPOSURE + READOUT"""
        ...

    def start_stream(self, frame_cb: Callable[[np.ndarray], None] | None = None) -> None: ...

    def stop_stream(self) -> None: ...

    def snap(self) -> np.ndarray: ...

    def acquire(self, frame_count: int) -> list[np.ndarray]: ...

    def set_range(self, vmin: float | None, vmax: float | None) -> None: ...

    def set_colormap(self, name: str) -> None: ...

    # Optional Hardware Controls
    def get_temperature(self) -> tuple: ...
    def set_temperature(self, celsius: float) -> None: ...
    def get_adc_candidates(self) -> dict: ...
    def get_adc_settings(self) -> dict: ...
    def set_adc_settings(self, **kwargs) -> None: ...
    def get_roi(self) -> tuple | None: ...
    def set_roi(self, x: int, y: int, width: int, height: int, hbin: int = 1, vbin: int = 1) -> None: ...
