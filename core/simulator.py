"""
core/simulator.py
가상 카메라 + 가상 모터 패널 — 실 하드웨어 없이 ScanTab 전체 흐름을 검증한다.

재현하는 물리:
  - 이미지: 512×512 uint16, Gaussian 빔 스팟 + 백그라운드 + 가우시안 노이즈 + 열적 드리프트
  - 모터: M1(오른쪽 대각선) / M2(위) / M3(왼쪽 대각선)
  - 가중치 비대칭: 각 모터 후진이 전진보다 작음 (캘리브레이션 워커가 검출해야 할 대상)
"""
from __future__ import annotations

from typing import List, Optional
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# 가상 카메라
# ─────────────────────────────────────────────────────────────────────────────

class SimCamera:
    """
    Gaussian 빔 스팟이 있는 합성 이미지를 생성하는 가상 카메라.

    SimMotorPanel.move() 호출 시 스팟 위치가 이동하며,
    snap() 할 때마다 미세한 열적 드리프트(random walk)가 추가된다.
    """

    WIDTH  = 512
    HEIGHT = 512

    def __init__(
        self,
        sigma:   float = 25.0,   # 빔 반경 (px)
        peak:    int   = 45000,  # 피크 강도 (uint16)
        bg:      int   = 500,    # 균일 배경
        noise:   float = 300.0,  # 가우시안 노이즈 sigma
        drift:   float = 0.03,   # 스냅당 열적 드리프트 (px rms)
        seed:    int   = 42,
    ):
        self._cx    = float(self.WIDTH  // 2)
        self._cy    = float(self.HEIGHT // 2)
        self._sigma = sigma
        self._peak  = peak
        self._bg    = bg
        self._noise = noise
        self._drift = drift
        self._rng   = np.random.default_rng(seed)

    # ── 내부 API (SimMotorPanel 전용) ─────────────────────────────────
    def _apply_move(self, dx: float, dy: float):
        # Ensure the direction of movement aligns with the motor steps
        self._cx = float(np.clip(self._cx - dx, 5, self.WIDTH  - 6))
        self._cy = float(np.clip(self._cy - dy, 5, self.HEIGHT - 6))

    # ── 공개 API (ScanTab / CalibWorker 인터페이스) ───────────────────
    def snap(self) -> np.ndarray:
        """uint16 (HEIGHT × WIDTH) 2D 배열 반환."""
        xs = np.arange(self.WIDTH,  dtype=np.float32)
        ys = np.arange(self.HEIGHT, dtype=np.float32)
        XX, YY = np.meshgrid(xs, ys)

        img  = self._peak * np.exp(
            -((XX - self._cx) ** 2 + (YY - self._cy) ** 2)
            / (2.0 * self._sigma ** 2)
        )
        img += self._bg + self._rng.normal(0.0, self._noise, img.shape)

        # 열적 드리프트 (random walk)
        self._cx += self._rng.normal(0.0, self._drift)
        self._cy += self._rng.normal(0.0, self._drift)

        return np.clip(img, 0, 65535).astype(np.uint16)

    def get_exposure_ms(self) -> float:
        return 100.0

    @property
    def spot_pos(self):
        """현재 스팟 위치 (cx, cy) — 디버그용."""
        return self._cx, self._cy


# ─────────────────────────────────────────────────────────────────────────────
# 가상 모터 패널
# ─────────────────────────────────────────────────────────────────────────────

class SimMotorPanel:
    """
    가상 Picomotor 패널.

    ScanTab / CalibWorker 가 사용하는 인터페이스만 구현:
      - is_connected (property)
      - move(motor_num, steps) -> bool
      - get_positions() -> list

    물리 모델 — 실제 MotorPanel 의 weight 구조를 그대로 복제:
      actual_steps = requested_steps * fwd_weight  (전진, steps > 0)
      actual_steps = requested_steps * bwd_weight  (후진, steps < 0)
      displacement = actual_steps * _SCALE * direction_vector

    기본값 (의도적으로 비대칭):
          fwd_w   bwd_w
      M1:  1.20   0.75   → 전진이 후진보다 큼
      M2:  1.00   0.90   → 후진이 약간 작음
      M3:  0.90   0.80   → 둘 다 1 미만이지만 서로 다름
      M4:  1.00   1.00   → 대칭 (기준축)
    """

    _SCALE = 5e-3   # 기준 px / step

    # 전진 방향 단위벡터 (dx, dy) — 정규화되지 않아도 됨
    _DIR = {
        1: ( 1.0,  0.6),   # M1: 오른쪽 대각선
        2: ( 0.0, -1.0),   # M2: 위
        3: (-1.0,  0.6),   # M3: 왼쪽 대각선
        4: ( 0.0,  0.0),   # M4: 무효
    }

    # 전진 / 후진 가중치 — 둘 다 독립적
    _FWD_W = {1: 1.20, 2: 1.00, 3: 0.90, 4: 1.00}
    _BWD_W = {1: 0.75, 2: 0.90, 3: 0.80, 4: 1.00}

    def __init__(self, cam: SimCamera):
        self._cam = cam
        self._pos = [0, 0, 0, 0]

    # ── ScanTab 인터페이스 ─────────────────────────────────────────
    @property
    def is_connected(self) -> bool:
        return True

    def move(self, motor_num: int, steps: int) -> bool:
        if motor_num < 1 or motor_num > 4:
            return False
        w = self._FWD_W[motor_num] if steps >= 0 else self._BWD_W[motor_num]
        vx, vy = self._DIR[motor_num]
        actual = steps * w                     # 실제로 모터에 전달되는 스텝
        dx = vx * self._SCALE * actual
        dy = vy * self._SCALE * actual
        self._pos[motor_num - 1] += steps      # 위치 카운터는 요청값 기준
        self._cam._apply_move(dx, dy)
        return True

    def get_positions(self) -> List[Optional[int]]:
        return list(self._pos)


# ─────────────────────────────────────────────────────────────────────────────
# AutoFocus 시뮬레이션 카메라
# ─────────────────────────────────────────────────────────────────────────────

class SimAFCamera:
    """
    오토포커스 시뮬레이션용 가상 카메라.

    두 가지 모드:
      "math"   — Gaussian 빔 + Z 거리에 비례하는 defocus blur
      "images" — 미리 로드된 이미지 배열을 Z 순서대로 반환

    사용법:
        # 수학 모델
        cam = SimAFCamera(mode="math", best_z=0.0, z_sigma=5.0)
        cam.set_z(z_value)
        frame = cam.snap()

        # 이미지 시퀀스
        cam = SimAFCamera(mode="images", images=[img0, img1, ...])
        cam.set_z_sequence([z0, z1, ...])
        cam.set_z(z_value)
        frame = cam.snap()
    """

    WIDTH  = 512
    HEIGHT = 512

    def __init__(
        self,
        mode:     str   = "math",    # "math" | "images"
        images:   Optional[List[np.ndarray]] = None,
        best_z:   float = 0.0,       # math 모드: 포커스가 맞는 Z (µm)
        z_sigma:  float = 8.0,       # math 모드: 디포커스 스케일 (µm당 blur px)
        peak:     int   = 40000,     # math 모드: 최대 강도
        bg:       int   = 300,
        noise:    float = 200.0,
        seed:     int   = 7,
    ):
        self._mode    = mode
        self._images  = images or []
        self._best_z  = best_z
        self._z_sigma = z_sigma
        self._peak    = peak
        self._bg      = bg
        self._noise   = noise
        self._rng     = np.random.default_rng(seed)
        self._current_z = best_z
        self._z_seq:  List[float] = []   # image 모드: Z → 인덱스 매핑

    def set_z(self, z: float):
        """현재 Z 위치 지정 — snap() 호출 전에 반드시 호출."""
        self._current_z = z

    def set_z_sequence(self, z_values: List[float]):
        """이미지 시퀀스 모드에서 Z 목록과 이미지 배열을 매핑."""
        self._z_seq = list(z_values)

    def snap(self) -> np.ndarray:
        """현재 Z에서 이미지 캡처 시뮬레이션."""
        if self._mode == "images":
            return self._snap_image()
        return self._snap_math()

    def get_exposure_ms(self) -> float:
        return 50.0

    # ── 내부 ────────────────────────────────────────────────────────────

    def _snap_math(self) -> np.ndarray:
        """Gaussian 빔 + defocus blur."""
        try:
            import cv2
            _CV2 = True
        except ImportError:
            _CV2 = False

        xs = np.arange(self.WIDTH,  dtype=np.float32)
        ys = np.arange(self.HEIGHT, dtype=np.float32)
        XX, YY = np.meshgrid(xs, ys)
        cx, cy  = self.WIDTH / 2.0, self.HEIGHT / 2.0

        sigma_beam = 20.0   # 빔 반경 (px)
        img = self._peak * np.exp(
            -((XX - cx) ** 2 + (YY - cy) ** 2) / (2.0 * sigma_beam ** 2)
        )
        img += self._bg + self._rng.normal(0.0, self._noise, img.shape)

        # Z 거리에 비례하는 defocus blur
        dz = abs(self._current_z - self._best_z)
        blur_sigma = dz / max(self._z_sigma, 0.001)   # µm → px
        if blur_sigma > 0.5 and _CV2:
            ks = max(3, int(blur_sigma * 4) | 1)   # 홀수 커널
            img = cv2.GaussianBlur(img.astype(np.float32), (ks, ks), blur_sigma)

        # 노이즈 추가 (블러 후에도)
        img += self._rng.normal(0.0, self._noise * 0.3, img.shape)

        return np.clip(img, 0, 65535).astype(np.uint16)

    def _snap_image(self) -> np.ndarray:
        """Z에 가장 가까운 이미지 인덱스를 찾아 반환."""
        if not self._images:
            return np.zeros((self.HEIGHT, self.WIDTH), dtype=np.uint16)

        if self._z_seq:
            # Z 시퀀스가 있으면 가장 가까운 인덱스
            diffs = [abs(z - self._current_z) for z in self._z_seq]
            idx = int(np.argmin(diffs))
        else:
            # 없으면 center부터 순서대로
            idx = 0

        idx = max(0, min(idx, len(self._images) - 1))
        img = self._images[idx]
        if img.dtype != np.uint16:
            # 스케일 정규화
            mn, mx = img.min(), img.max()
            if mx > mn:
                img = ((img.astype(np.float32) - mn) / (mx - mn) * 65535).astype(np.uint16)
            else:
                img = np.zeros_like(img, dtype=np.uint16)
        return img
