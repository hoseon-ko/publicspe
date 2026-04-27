"""
core/camera/base.py
카메라 추상 기반 클래스 + 기능 선언(Capabilities) 패턴.

각 카메라 구현체는 BaseCamera를 상속하고
capabilities 프로퍼티를 통해 지원 기능을 선언한다.
UI는 이 정보를 기반으로 컨트롤을 동적으로 표시/숨긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


class NotSupportedError(RuntimeError):
    """해당 카메라에서 지원하지 않는 기능을 호출했을 때 발생."""


@dataclass
class CameraCapabilities:
    # ── 공통 ──────────────────────────────────────────────────────────
    has_roi: bool = False
    exposure_range_ms: tuple = (0.01, 1_000_000.0)

    # ── HIKVISION (소프트웨어/SDK 레벨) ──────────────────────────────
    has_fps_control: bool = False
    fps_range: tuple = (0.1, 1000.0)
    has_binarize: bool = False
    has_log_scale: bool = False
    has_bg_subtraction: bool = False
    has_centroid: bool = False

    # ── Picam (하드웨어 레벨) ─────────────────────────────────────────
    has_temperature: bool = False
    temperature_range_c: tuple = (None, None)
    has_adc: bool = False
    adc_quality_options: List[str] = field(default_factory=list)
    adc_speed_options: List[str] = field(default_factory=list)
    adc_gain_options: List[str] = field(default_factory=list)
    adc_bit_depth_options: List[str] = field(default_factory=list)
    adc_port_options: List[str] = field(default_factory=list)

    # ── 소프트웨어 처리 (모든 카메라 공통) ───────────────────────────
    has_spatial_filter:   bool = True   # 공간 필터 (핫픽셀/가우시안/미디언)
    has_display_stretch:  bool = True   # Display 스트레칭 모드 선택
    has_dark_flat:        bool = True   # Dark frame / Flat field 보정


class BaseCamera:
    """
    모든 카메라 구현체의 공통 인터페이스.

    connect / disconnect / snap / start_live / stop_live 만 필수.
    나머지는 capabilities 선언 후 구현하거나, NotSupportedError를 올린다.
    """

    # ── 필수 구현 ─────────────────────────────────────────────────────

    @property
    def capabilities(self) -> CameraCapabilities:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        return False

    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def get_exposure_ms(self) -> float:
        raise NotImplementedError

    def set_exposure_ms(self, ms: float) -> float:
        raise NotImplementedError

    def snap(self) -> Any:
        """단일 프레임 취득 (numpy ndarray 반환)."""
        raise NotImplementedError

    def start_live(self, frame_cb: Callable[[Any], None]) -> None:
        """실시간 프레임 스트림 시작. 새 프레임마다 frame_cb(ndarray) 호출."""
        raise NotImplementedError

    def stop_live(self) -> None:
        raise NotImplementedError

    # ── 선택적 구현 (미지원 시 NotSupportedError) ─────────────────────

    def set_fps(self, fps: float) -> float:
        raise NotSupportedError("FPS control not supported")

    def get_fps(self) -> float:
        raise NotSupportedError("FPS control not supported")

    def set_roi(self, x: int, y: int, width: int, height: int,
                hbin: int = 1, vbin: int = 1) -> None:
        raise NotSupportedError("ROI not supported")

    def get_roi(self) -> Optional[tuple]:
        raise NotSupportedError("ROI not supported")

    # ── Picam 전용 ────────────────────────────────────────────────────

    def set_temperature(self, celsius: float) -> None:
        raise NotSupportedError("Temperature control not supported")

    def get_temperature(self) -> tuple:
        """(reading, setpoint, status) 반환."""
        raise NotSupportedError("Temperature control not supported")

    def set_adc_settings(self, **kwargs: Any) -> None:
        raise NotSupportedError("ADC settings not supported")

    def get_adc_candidates(self) -> dict:
        raise NotSupportedError("ADC settings not supported")

    # ── 저장 헬퍼 ────────────────────────────────────────────────────

    def camera_name(self) -> str:
        return "Camera"

    def camera_model(self) -> str:
        return ""

    def camera_serial(self) -> str:
        return ""
