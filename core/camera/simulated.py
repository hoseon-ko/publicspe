"""core/camera/simulated.py
소프트웨어 카메라 시뮬레이션.

하드웨어 없이 전체 UI 동작을 확인할 수 있도록 모든 기능(온도/ADC/FPS/ROI)을
활성화하고, 이동하는 가우시안 피크 + 스펙트럼 줄무늬 + 포아송 노이즈로
구성된 애니메이션 프레임을 생성한다.
8-bit, 12-bit, 16-bit 모드를 지원한다.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np


from core.camera.base import BaseCamera, CameraCapabilities


def is_available() -> bool:
    return True


def list_devices() -> list[str]:
    return [
        "[SIM-0]  Simulated Camera (16-bit)  512×512",
        "[SIM-1]  Simulated Camera (12-bit)  512×512",
        "[SIM-2]  Simulated Camera (8-bit)   512×512",
    ]


class SimulatedCamera(BaseCamera):
    """
    애니메이션 테스트 패턴을 생성하는 소프트웨어 시뮬레이션 카메라.

    · 이동하는 가우시안 주/부 피크  — centroid, colormap, range slider 테스트
    · 수평 스펙트럼 줄무늬          — 히스토그램, 프로파일 플롯 테스트
    · 포아송 배경 노이즈             — 통계 계산 테스트
    · 온도 드리프트 시뮬레이션       — temperature polling 테스트
    · ADC 옵션 노출                  — ADC 패널 테스트
    """

    _W = 512
    _H = 512

    def __init__(self, bit_depth: int = 16):
        self._connected   = False
        self._exposure_ms = 100.0
        self._fps         = 10.0
        self._bit_depth   = bit_depth
        self.simulate_gil_block = False  # 테스트용 GIL 독점 모드

        # 온도: 실온에서 setpoint 로 서서히 냉각
        self._setpoint    = -70.0
        self._reading     = 22.0
        self._temp_status = "Unlocked"

        self._adc: dict = {
            "adc_quality":     "Low Noise",
            "adc_speed":       "100kHz",
            "adc_analog_gain": "1x",
            "bit_depth":       f"{bit_depth}bit",
        }

        self._stop_evt    = threading.Event()
        self._live_thread: Optional[threading.Thread] = None
        self._phase       = 0.0   # 애니메이션 위상 누산기
        self._temp_lock   = threading.Lock()  # 온도 상태 read-modify-write 보호

        # 매 프레임마다 재할당하지 않도록 ogrid 캐싱
        self._yy, self._xx = np.ogrid[0:self._H, 0:self._W]

        self._caps = CameraCapabilities(
            has_roi               = True,
            exposure_range_ms     = (0.001, 3_600_000.0),
            has_fps_control       = True,
            fps_range             = (0.5, 1000.0),
            has_binarize          = True,
            has_log_scale         = True,
            has_bg_subtraction    = True,
            has_centroid          = True,
            has_temperature       = True,
            temperature_range_c   = (-100.0, 30.0),
            has_adc               = True,
            adc_quality_options   = ["Low Noise", "High Capacity"],
            adc_speed_options     = ["100kHz", "1MHz", "2MHz"],
            adc_gain_options      = ["1x", "2x", "4x"],
            adc_bit_depth_options = [f"{bit_depth}bit"],
            has_spatial_filter    = True,
            has_display_stretch   = True,
            has_dark_flat         = True,
        )

    # ── 식별자 ───────────────────────────────────────────────────────

    def camera_name(self)  -> str: return f"Simulated Camera ({self._bit_depth}-bit)"
    def camera_model(self) -> str: return f"SIM-{self._bit_depth}"
    def camera_serial(self)-> str: return f"SIM-{self._bit_depth:02d}0001"

    # ── 상태 ─────────────────────────────────────────────────────────

    @property
    def capabilities(self) -> CameraCapabilities:
        return self._caps

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── 연결 ─────────────────────────────────────────────────────────

    def connect(self) -> None:
        time.sleep(0.35)          # 연결 지연 시뮬레이션
        self._connected = True

    def disconnect(self) -> None:
        self.stop_live()
        self._connected = False

    # ── 노출 / FPS ───────────────────────────────────────────────────

    def get_exposure_ms(self) -> float:
        return self._exposure_ms

    def set_exposure_ms(self, ms: float) -> float:
        self._exposure_ms = float(ms)
        return self._exposure_ms

    def _get_frame_total_s(self) -> float:
        """가상 리드아웃 시간(45ms) 포함 프레임 소요 시간"""
        return (self._exposure_ms / 1000.0) + 0.045

    def get_fps(self) -> float:
        return self._fps

    def set_fps(self, fps: float) -> float:
        self._fps = max(0.5, min(60.0, float(fps)))
        return self._fps

    def disable_fps_lock(self) -> None:
        pass

    # ── ROI ──────────────────────────────────────────────────────────

    def get_roi(self):
        return (0, 0, self._W, self._H, 1, 1)

    def set_roi(self, x, y, width, height, hbin=1, vbin=1) -> None:
        pass   # 시뮬레이터는 ROI 파라미터 무시

    # ── 온도 ─────────────────────────────────────────────────────────

    def get_temperature(self):
        """(reading, setpoint, status) — setpoint 로 exponential 냉각."""
        with self._temp_lock:
            diff = self._setpoint - self._reading
            self._reading += diff * 0.04      # 3초 간격 폴링 기준 ~30스텝에 수렴
            if abs(diff) < 0.5:
                self._temp_status = "Locked"
            else:
                self._temp_status = "Cooling" if diff < 0 else "Unlocked"
            return round(self._reading, 2), self._setpoint, self._temp_status

    def set_temperature(self, celsius: float) -> None:
        with self._temp_lock:
            self._setpoint = float(celsius)
            self._temp_status = "Unlocked"

    # ── ADC ──────────────────────────────────────────────────────────

    def get_adc_settings(self) -> dict:
        return dict(self._adc)

    def set_adc_settings(self, **kwargs) -> None:
        self._adc.update({k: v for k, v in kwargs.items() if v is not None})

    # ── 촬영 ─────────────────────────────────────────────────────────

    def snap(self) -> np.ndarray:
        import time
        # [Phase 6] 실제 노출 시간 + 리드아웃에 비례하여 대기
        wait_time = self._get_frame_total_s()
        if getattr(self, 'simulate_gil_block', False):
            print(f"\n[SimCam] BAD mode: GIL blocking simulation ({wait_time}s)...")
            print("[SimCam] UI may not respond during this wait.")
            t0 = time.time()
            while time.time() - t0 < wait_time:
                pass  # CPU 100% 사용 & GIL 선점
        else:
            print(f"\n[SimCam] GOOD mode: short polling + sleep ({wait_time}s)...")
            print("[SimCam] UI can keep responding during this wait.")
            t0 = time.time()
            while time.time() - t0 < wait_time:
                time.sleep(0.01)  # GIL 강제 양보
                
        print("[SimCam] frame returned\n")
        return self._make_frame()

    def start_live(self, frame_cb: Callable[[np.ndarray], None]) -> None:
        if self._live_thread is not None and self._live_thread.is_alive():
            return  # 이미 실행 중 — 이중 호출 무시
        self._stop_evt.clear()
        self._live_thread = threading.Thread(
            target=self._live_loop,
            args=(frame_cb,),
            daemon=True,
            name="SimCamLive",
        )
        self._live_thread.start()

    def stop_live(self) -> None:
        self._stop_evt.set()
        if self._live_thread is not None:
            self._live_thread.join(timeout=2.0)
            self._live_thread = None

    # ── 프레임 생성 ──────────────────────────────────────────────────

    def _live_loop(self, frame_cb: Callable[[np.ndarray], None]) -> None:
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            
            # 1. 하드웨어 물리적 지연 (노출 + 리드아웃)
            hardware_delay = self._get_frame_total_s()
            
            if getattr(self, 'simulate_gil_block', False):
                # [BAD] Busy-wait (GIL 선점 시도)
                while time.monotonic() - t0 < hardware_delay:
                    if self._stop_evt.is_set(): break
            else:
                # [GOOD] Event wait (GIL 양보)
                if self._stop_evt.wait(hardware_delay):
                    break
                
            if self._stop_evt.is_set(): break
            frame_cb(self._make_frame())

            
            # 2. 지정된 FPS 한계가 더 길면 추가 대기
            elapsed = time.monotonic() - t0
            target_frame_time = 1.0 / self._fps
            sleep = max(0.0, target_frame_time - elapsed)
            if sleep > 0:
                self._stop_evt.wait(sleep)

    def _make_frame(self) -> np.ndarray:
        """
        이동하는 가우시안 피크(주/부) + 스펙트럼 줄무늬 + 포아송 노이즈.
        노출 시간에 비례해 신호 강도가 변한다.
        bit_depth에 따라 최대값을 클리핑하고 데이터 타입을 결정한다.
        """
        self._phase += 0.07
        p = self._phase
        H, W = self._H, self._W
        yy, xx = np.ogrid[0:H, 0:W]

        # 16비트 기준 최대 신호 (노이즈 포함 ~65000)
        max_val_16bit = 65535.0
        target_max = (2 ** self._bit_depth) - 1

        # 배경 — 포아송 판독 노이즈 (비트 깊이에 맞춰 스케일링)
        base_noise = 250.0 * (target_max / max_val_16bit)
        img = np.random.poisson(base_noise, (H, W)).astype(np.float32)

        # 주 피크
        cx = W // 2 + int(W * 0.28 * np.sin(p * 0.23))
        cy = H // 2 + int(H * 0.20 * np.cos(p * 0.17))
        σ  = 26.0 + 10.0 * np.sin(p * 0.37)
        peak_amp = 52000.0 * (target_max / max_val_16bit)
        img += peak_amp * np.exp(-((xx - cx)**2 + (yy - cy)**2) / (2 * σ**2))

        # 부 피크
        cx2 = cx + int(45 * np.cos(p * 0.11))
        cy2 = cy + int(25 * np.sin(p * 0.19))
        img += (16000.0 * (target_max / max_val_16bit)) * np.exp(-((xx - cx2)**2 + (yy - cy2)**2) / (2 * 16.0**2))

        # 수평 스펙트럼 줄무늬
        img += (1800.0 * (target_max / max_val_16bit)) * np.sin(2 * np.pi * xx / W * 9 + p * 0.6) ** 2

        # 노출 배율
        img *= min(3.0, max(0.05, self._exposure_ms / 100.0))

        if self._bit_depth <= 8:
            return np.clip(img, 0, 255).astype(np.uint8)
        else:
            return np.clip(img, 0, target_max).astype(np.uint16)
