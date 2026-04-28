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
        self._cx = float(np.clip(self._cx + dx, 5, self.WIDTH  - 6))
        self._cy = float(np.clip(self._cy + dy, 5, self.HEIGHT - 6))

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

    물리 모델:
      M1: 오른쪽 대각선  (+dx, +dy)   bwd_weight = 0.75
      M2: 위              (0,   -dy)   bwd_weight = 0.90
      M3: 왼쪽 대각선    (-dx, +dy)   bwd_weight = 0.80
      M4: 효과 없음                   bwd_weight = 1.00

    전진 px/step 은 모든 모터 동일(5e-3 px/step), 방향만 다름.
    후진은 전진 대비 bwd_weight 비율만큼 이동 → 비대칭 재현.
    """

    _SCALE = 5e-3   # px per step (전진)

    # 전진 방향 단위벡터 (dx, dy)
    _FWD_DIR = {
        1: ( 1.0,  0.6),   # M1: 오른쪽 + 약간 아래
        2: ( 0.0, -1.0),   # M2: 위
        3: (-1.0,  0.6),   # M3: 왼쪽 + 약간 아래
        4: ( 0.0,  0.0),   # M4: 무효
    }

    # 후진 가중치 (1.0 = 완전 대칭)
    _BWD_WEIGHT = {1: 0.75, 2: 0.90, 3: 0.80, 4: 1.00}

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
        vx, vy = self._FWD_DIR[motor_num]
        if steps >= 0:
            dx = vx * self._SCALE * steps
            dy = vy * self._SCALE * steps
        else:
            w  = self._BWD_WEIGHT[motor_num]
            dx = vx * self._SCALE * steps * w   # steps < 0 → 반대 방향, 스케일 축소
            dy = vy * self._SCALE * steps * w
        self._pos[motor_num - 1] += steps
        self._cam._apply_move(dx, dy)
        return True

    def get_positions(self) -> List[Optional[int]]:
        return list(self._pos)
