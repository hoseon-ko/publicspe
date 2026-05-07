"""
core/motor/kinematic_calc.py
AlignStage 키네마틱 계산기.

KinematicSimulator_v2.py 의 calculate_position() 로직을 분리한 모듈.
AlignStageAlgorithm.py (numpy/scipy 순수 Python)에 의존하며 C# DLL 불필요.

축 매핑 (calPos 인덱스):
  [0] Y1 (Ax0) → ACSC_AXIS_1
  [1] Z1 (Ax1) → ACSC_AXIS_2
  [2] X1 (Ax4) → ACSC_AXIS_3
  [3] Z2 (Ax5) → ACSC_AXIS_4
  [4] Y2 (Ax8) → ACSC_AXIS_5
  [5] Z3 (Ax9) → ACSC_AXIS_6
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# AlignStageAlgorithm.py는 프로젝트 루트에 위치
_PROJ_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

try:
    from AlignStageAlgorithm import CalculateBallPositionPivot
    _ALGO_OK = True
except Exception as e:
    _ALGO_OK = False
    log.warning(f"[Kinematic] AlignStageAlgorithm import 실패: {e}")


def is_available() -> bool:
    return _ALGO_OK


# ── 기본 파라미터 (KinematicSimulator_v2.py XML 설정 기준) ─────────────────────

DEFAULT_PIVOT = np.array([-280.4054, 940.34, 1525.1744])
DEFAULT_BEAM_Z_PATH_DEG = 4.0

DEFAULT_STAGE_SETUP_POS = np.array([
     5.3867,    0.0,   796.0,
  -346.3333,    0.0,  -398.0,
   340.9467,    0.0,  -398.0,
], dtype=float)

DEFAULT_STAGE_ENCODER_POS = np.array([
    [1277.5,   -1513.68,    0.0  ],
    [   0.0,   -1592.31,  804.45 ],
    [1052.41,  -1433.52,    0.0  ],
], dtype=float)

DEFAULT_DIRECTION = np.array([
    [ 1.,  1.,  1.],
    [ 1.,  1.,  1.],
    [-1.,  1.,  1.],
], dtype=float)

# Slave mapping (sm1X..sm3Z)
_DEFAULT_MAPPING = np.array([
    0., 0., 1.,
    1., 0., 0.,
    0., 0., 1.,
], dtype=float)

# Soft limits [minus, plus] per axis
DEFAULT_PLUS_LIMITS  = np.array([1287.5, -1503.68,  814.45, -1582.31,  1062.41, -1423.52])
DEFAULT_MINUS_LIMITS = np.array([1267.5, -1523.68,  794.45, -1602.31,  1042.41, -1443.52])

MOTOR_NAMES = ["Y1(Ax0)", "Z1(Ax1)", "X1(Ax4)", "Z2(Ax5)", "Y2(Ax8)", "Z3(Ax9)"]


class KinematicCalc:
    """
    6DOF 입력 → 6축 모터 CalPos 변환기.

    사용법:
        calc = KinematicCalc()
        cal_pos, ball, ok, violations = calc.calculate([0,0,0], [0,0,0])
        # trans_mm: [X, Y, Z] mm
        # rotate_mrad: [Rx, Ry, Rz] mrad
    """

    def __init__(self):
        self.pivot          = DEFAULT_PIVOT.copy()
        self.beam_z_deg     = DEFAULT_BEAM_Z_PATH_DEG
        self.stage_setup    = DEFAULT_STAGE_SETUP_POS.copy()
        self.encoder_pos    = DEFAULT_STAGE_ENCODER_POS.copy()
        self.direction      = DEFAULT_DIRECTION.copy()
        self.plus_limits    = DEFAULT_PLUS_LIMITS.copy()
        self.minus_limits   = DEFAULT_MINUS_LIMITS.copy()
        self._mapping       = _DEFAULT_MAPPING.copy()

    def calculate(
        self,
        trans_mm: list[float],
        rotate_mrad: list[float],
        pivot_override: Optional[np.ndarray] = None,
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray], bool, list[str]]:
        """
        6DOF → 6축 CalPos 계산.

        Returns:
            cal_pos    : np.ndarray[6] (mm), 실패 시 None
            ball_pos   : np.ndarray[3×3] 볼 위치, 실패 시 None
            interlock_ok: 소프트 리밋 통과 여부
            violations : 위반 내용 문자열 리스트
        """
        if not _ALGO_OK:
            return None, None, False, ["AlignStageAlgorithm 모듈 로드 실패"]

        try:
            r = np.array(rotate_mrad, dtype=float) / 1000.0  # mrad → rad
            t = np.array(trans_mm, dtype=float)
            ssp = self.stage_setup.copy()
            piv = pivot_override if pivot_override is not None else self.pivot

            ball_raw = CalculateBallPositionPivot(
                r, t,
                ssp, ssp,
                self._mapping, ssp,
                self._mapping, ssp,
                piv,
            )

            ball = ball_raw.reshape(3, 3)        # 볼 위치 (3 stage × XYZ)
            ssp3 = ssp.reshape(3, 3)             # 셋업 기준 위치
            enc  = self.encoder_pos              # 엔코더 영점 (3 stage × XYZ)
            d    = self.direction                # 모터 회전 방향 (+1 / -1)

            # 각 stage 별 변위 = (회전된 볼 위치) - (셋업 기준 위치)
            # stage 0(=Y1/Z1)은 Y,Z 두 축 / stage 1(=X1/Z2)은 X,Z / stage 2(=Y2/Z3)은 Y,Z
            final = np.zeros((3, 2))
            final[0, 0] = ball[0, 0] - ssp3[0, 0]   # Stage0: ΔY
            final[0, 1] = ball[0, 1] - ssp3[0, 1]   # Stage0: ΔZ
            final[1, 0] = ball[1, 2] - ssp3[1, 2]   # Stage1: ΔX (인덱스 2 = X 컴포넌트)
            final[1, 1] = ball[1, 1] - ssp3[1, 1]   # Stage1: ΔZ
            final[2, 0] = ball[2, 0] - ssp3[2, 0]   # Stage2: ΔY
            final[2, 1] = ball[2, 1] - ssp3[2, 1]   # Stage2: ΔZ

            # 모터 절대 명령값 = 엔코더 기준 + 방향부호×변위
            # (열 인덱스는 enc/d의 XYZ 컴포넌트 — 모터가 실제 움직이는 축)
            cal_pos = np.array([
                enc[0, 0] + final[0, 0] * d[0, 0],  # Y1 (stage0 Y축)
                enc[0, 1] + final[0, 1] * d[0, 1],  # Z1 (stage0 Z축)
                enc[1, 2] + final[1, 0] * d[1, 2],  # X1 (stage1 X축)
                enc[1, 1] + final[1, 1] * d[1, 1],  # Z2 (stage1 Z축)
                enc[2, 0] + final[2, 0] * d[2, 0],  # Y2 (stage2 Y축)
                enc[2, 1] + final[2, 1] * d[2, 1],  # Z3 (stage2 Z축)
            ])

            ok, violations = self.check_interlock(cal_pos)
            return cal_pos, ball, ok, violations

        except Exception as e:
            log.error(f"[Kinematic] calculate error: {e}")
            return None, None, False, [str(e)]

    def check_interlock(self, cal_pos: np.ndarray) -> tuple[bool, list[str]]:
        violations = []
        for pos, name, plus, minus in zip(
            cal_pos, MOTOR_NAMES, self.plus_limits, self.minus_limits
        ):
            if pos > plus:
                violations.append(f"{name}: {pos:.4f} > +Limit {plus}")
            elif pos < minus:
                violations.append(f"{name}: {pos:.4f} < -Limit {minus}")
        return len(violations) == 0, violations

    def beam_pivot(self, beam_angle_mrad: float) -> np.ndarray:
        """Beam Angle(mrad) 적용 pivot 위치 반환."""
        bz_rad = self.beam_z_deg * (np.pi / 180.0)
        bpath  = beam_angle_mrad / 1000.0
        return np.array([
            self.pivot[0],
            self.pivot[1] + bpath * np.sin(bz_rad) * -1,
            self.pivot[2] + bpath * np.cos(bz_rad),
        ])
