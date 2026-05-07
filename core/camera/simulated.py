"""core/camera/simulated.py
소프트웨어 카메라 시뮬레이션.

하드웨어 없이 전체 UI 동작을 확인할 수 있도록 모든 기능(온도/ADC/FPS/ROI)을
활성화하고, 이동하는 가우시안 피크 + 스펙트럼 줄무늬 + 포아송 노이즈로
구성된 16-bit 애니메이션 프레임을 생성한다.
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
    return ["[SIM-0]  Simulated Camera  512×512  16-bit"]


class SimulatedCamera(BaseCamera):
    """
    애니메이션 테스트 패턴을 생성하는 소프트웨어 시뮬레이션 카메라.

    · 이동하는 가우시안 주/부 피크  — centroid, colormap, range slider 테스트
    · 수평 스펙트럼 줄무늬          — 히스토그램, 프로파일 플롯 테스트
    · 포아송 배경 노이즈             — 통계 계산 테스트
    · 온도 드리프트 시뮬레이션       — temperature polling 테스트
    · ADC 옵션 노출                  — ADC 패널 테스트
    """

    _W = 2048
    _H = 2048

    def __init__(self):
        self._connected   = False
        self._exposure_ms = 100.0
        self._fps         = 10.0
        self.simulate_gil_block = False  # 테스트용 GIL 독점 모드

        # 온도: 실온에서 setpoint 로 서서히 냉각
        self._setpoint    = -70.0
        self._reading     = 22.0
        self._temp_status = "Unlocked"

        self._adc: dict = {
            "adc_quality":     "Low Noise",
            "adc_speed":       "100kHz",
            "adc_analog_gain": "1x",
            "bit_depth":       "16bit",
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
            adc_bit_depth_options = ["16bit"],
            has_spatial_filter    = True,
            has_display_stretch   = True,
            has_dark_flat         = True,
        )

    # ── 식별자 ───────────────────────────────────────────────────────

    def camera_name(self)  -> str: return "Simulated Camera"
    def camera_model(self) -> str: return "SIM-512"
    def camera_serial(self)-> str: return "SIM-000001"

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
        wait_time = 5.0
        if getattr(self, 'simulate_gil_block', False):
            print(f"\n[SimCam] 🚨 BAD 모드: GIL 독점 시뮬레이션 ({wait_time}초)...")
            print("이 시간 동안 창을 드래그하거나 UI를 클릭해도 반응하지 않습니다.")
            t0 = time.time()
            while time.time() - t0 < wait_time:
                pass  # CPU 100% 사용 & GIL 선점
        else:
            print(f"\n[SimCam] ✅ GOOD 모드: 짧은 폴링 + Sleep ({wait_time}초)...")
            print("기다리는 동안에도 UI가 부드럽게 반응합니다.")
            t0 = time.time()
            while time.time() - t0 < wait_time:
                time.sleep(0.01)  # GIL 강제 양보
                
        print("[SimCam] 📸 프레임 반환 완료\n")
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
            frame_cb(self._make_frame())
            sleep = max(0.0, 1.0 / self._fps - (time.monotonic() - t0))
            self._stop_evt.wait(sleep)

    def _make_frame(self) -> np.ndarray:
        """
        이동하는 가우시안 피크(주/부) + 스펙트럼 줄무늬 + 포아송 노이즈.
        노출 시간에 비례해 신호 강도가 변한다 (1:1 기준 100ms).
        """
        self._phase += 0.07
        p = self._phase
        H, W = self._H, self._W
        yy, xx = np.ogrid[0:H, 0:W]

        # 배경 — 포아송 판독 노이즈
        img = np.random.poisson(250, (H, W)).astype(np.float32)

        # 주 피크 — 리사주 궤적으로 이동
        cx = W // 2 + int(W * 0.28 * np.sin(p * 0.23))
        cy = H // 2 + int(H * 0.20 * np.cos(p * 0.17))
        σ  = 26.0 + 10.0 * np.sin(p * 0.37)
        img += 52000.0 * np.exp(-((xx - cx)**2 + (yy - cy)**2) / (2 * σ**2))

        # 부 피크 — 주 피크 근방에서 진동
        cx2 = cx + int(45 * np.cos(p * 0.11))
        cy2 = cy + int(25 * np.sin(p * 0.19))
        img += 16000.0 * np.exp(-((xx - cx2)**2 + (yy - cy2)**2) / (2 * 16.0**2))

        # 수평 스펙트럼 줄무늬 (프로파일 플롯용)
        img += 1800.0 * np.sin(2 * np.pi * xx / W * 9 + p * 0.6) ** 2

        # 노출 배율 (100ms 기준 선형 스케일)
        img *= min(3.0, max(0.05, self._exposure_ms / 100.0))

        return np.clip(img, 0, 65535).astype(np.uint16)
