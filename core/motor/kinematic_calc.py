"""
core/motor/kinematic_calc.py
AlignStage 키네마틱 계산기.

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
    from AlignStageAlgorithm import CalculateBallPositionPivot, CalculateAttitudePivot
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
    [ 1277.5,  -1513.68, 0], # Stage 1 (X, Y, Z)
    [ 0.0,  -1592.31, 804.45], # Stage 2 (X, Y, Z)
    [ 1052.41, -1433.52, 0], # Stage 3 (X, Y, Z)
], dtype=float)
 
DEFAULT_DIRECTION = np.array([
    [ 1.,  1.,  1.], # Stage 1 (X, Y, Z)
    [ 1.,  1.,  1.], # Stage 2 (X, Y, Z)
    [-1.,  1.,  1.], # Stage 3 (X, Y, Z) -> X is -1 per screenshot
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
            # Stage 0 (Ball 1): X, Y 성분 사용
            # Stage 1 (Ball 2): Z, Y 성분 사용
            # Stage 2 (Ball 3): X, Y 성분 사용
            final = np.zeros((3, 2))
            final[0, 0] = ball[0, 0] - ssp3[0, 0]   # Ball0: ΔX
            final[0, 1] = ball[0, 1] - ssp3[0, 1]   # Ball0: ΔY
            final[1, 0] = ball[1, 2] - ssp3[1, 2]   # Ball1: ΔZ
            final[1, 1] = ball[1, 1] - ssp3[1, 1]   # Ball1: ΔY
            final[2, 0] = ball[2, 0] - ssp3[2, 0]   # Ball2: ΔX
            final[2, 1] = ball[2, 1] - ssp3[2, 1]   # Ball2: ΔY

            # 모터 절대 명령값 = 엔코더 기준 + (변위 * 방향부호)
            # cal_pos 순서: X1, Y1, Z1, Z2, X2, Y2
            cal_pos = np.array([
                enc[0, 0] + final[0, 0] * d[0, 0],  # Motor 0: X1 (Stage 0, ΔX)
                enc[0, 1] + final[0, 1] * d[0, 1],  # Motor 1: Y1 (Stage 0, ΔY)
                enc[1, 2] + final[1, 0] * d[1, 2],  # Motor 2: Z1 (Stage 1, ΔZ)
                enc[1, 1] + final[1, 1] * d[1, 1],  # Motor 3: Z2 (Stage 1, ΔY)
                enc[2, 0] + final[2, 0] * d[2, 0],  # Motor 4: X2 (Stage 2, ΔX)
                enc[2, 1] + final[2, 1] * d[2, 1],  # Motor 5: Y2 (Stage 2, ΔY)
            ])

            ok, violations = self.check_interlock(cal_pos)
            
            return np.round(cal_pos, 4), ball, ok, violations

        except Exception as e:
            log.error(f"[Kinematic] calculate error: {e}")
            return None, None, False, [str(e)]

    def calculate_forward(
        self,
        motor_positions: np.ndarray,
        pivot_override: Optional[np.ndarray] = None,
        x0: Optional[np.ndarray] = None,
    ) -> Optional[np.ndarray]:
        """
        6축 모터 위치 → 6DOF [Rx, Ry, Rz, Tx, Ty, Tz] (rad/mm) 계산.
        
        Args:
            motor_positions: [X1, Y1, Z1, Z2, X2, Y2] (mm)
            x0: 초기 추정값 [Rx, Ry, Rz, Tx, Ty, Tz], None일 경우 0으로 시작.
        """
        if not _ALGO_OK: return None
        
        try:
            # 입력받은 모터 위치값을 소수점 4째자리로 제한 (사용자 요청 반영)
            motor_positions = np.round(motor_positions, 4)
            enc = self.encoder_pos
            ssp3 = self.stage_setup.reshape(3, 3)
            d = self.direction
            piv = pivot_override if pivot_override is not None else self.pivot
            mapping = self._mapping.reshape(3, 3)
            # 1. 모터 위치 → 볼 위치 (b1, b2, b3) 역산
            # b = [X1, Y1, Z1, X2, Y2, Z2, X3, Y3, Z3]
            b = np.zeros(9)
            
            # Stage 0 (Ball 1): Motor 0(X1) -> ΔX, Motor 1(Y1) -> ΔY
            b[0] = (motor_positions[0] - enc[0, 0]) * d[0, 0] + ssp3[0, 0] if mapping[0,0] == 0 else ssp3[0, 0] # X
            b[1] = (motor_positions[1] - enc[0, 1]) * d[0, 1] + ssp3[0, 1] if mapping[0,1] == 0 else ssp3[0, 1] # Y
            b[2] = (motor_positions[0] - enc[0, 2]) * d[0, 2] + ssp3[0, 2] if mapping[0,2] == 0 else ssp3[0, 2] # Z
        
            # Stage 1 (Ball 2): Motor 2(Z1) -> ΔZ, Motor 3(Z2) -> ΔY
            b[3] = (motor_positions[2] - enc[1, 0]) * d[1, 0] + ssp3[1, 0] if mapping[1,0] == 0 else ssp3[1, 0] # X
            b[4] = (motor_positions[3] - enc[1, 1]) * d[1, 1] + ssp3[1, 1] if mapping[1,1] == 0 else ssp3[1, 1] # Y
            b[5] = (motor_positions[2] - enc[1, 2]) * d[1, 2] + ssp3[1, 2] if mapping[1,2] == 0 else ssp3[1, 2] # Z
            
            # Stage 2 (Ball 3): Motor 4(X2) -> ΔX, Motor 5(Y2) -> ΔY
            b[6] = (motor_positions[4] - enc[2, 0]) * d[2, 0] + ssp3[2, 0] if mapping[2,0] == 0 else ssp3[2, 0] # X
            b[7] = (motor_positions[5] - enc[2, 1]) * d[2, 1] + ssp3[2, 1] if mapping[2,1] == 0 else ssp3[2, 1] # Y
            b[8] = (motor_positions[4] - enc[2, 2]) * d[2, 2] + ssp3[2, 2] if mapping[2,2] == 0 else ssp3[2, 2] # Z
            
            if x0 is None:
                x0 = np.zeros(6)
            
            # 2. 최적화 알고리즘 호출 (Rx, Ry, Rz, Tx, Ty, Tz 순서)
            res = CalculateAttitudePivot(
                self.stage_setup, self.stage_setup,
                self._mapping, self.stage_setup,
                self._mapping, self.stage_setup,
                b, x0, piv
            )
            return res # [Rx, Ry, Rz, Tx, Ty, Tz]
            
        except Exception as e:
            log.error(f"[Kinematic] calculate_forward error: {e}")
            return None

    def calculate_clamped(
        self,
        trans_mm: list[float],
        rotate_mrad: list[float],
        pivot_override: Optional[np.ndarray] = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        리밋을 반영한 실제 도달 가능한 DOF 계산.
        Returns: (clamped_motor_positions, actual_dof [Tx, Ty, Tz, Rx, Ry, Rz])
        """
        # 1. 역기네마틱 계산
        cal_pos, _, _, _ = self.calculate(trans_mm, rotate_mrad, pivot_override)
        if cal_pos is None:
            return None, None
            
        # 2. 리밋 클램핑
        clamped = np.clip(cal_pos, self.minus_limits, self.plus_limits)
        
        # 3. 순기네마틱 계산 (실제 도달 위치 추정)
        # x0는 원래 요청값으로 설정하여 수렴 속도 향상
        x0 = np.array([
            rotate_mrad[0]/1000.0, rotate_mrad[1]/1000.0, rotate_mrad[2]/1000.0,
            trans_mm[0], trans_mm[1], trans_mm[2]
        ])
        res = self.calculate_forward(clamped, pivot_override, x0)
        
        if res is not None:
            # res: [Rx, Ry, Rz, Tx, Ty, Tz] -> actual: [Tx, Ty, Tz, Rx, Ry, Rz]
            actual = np.array([res[3], res[4], res[5], res[0]*1000.0, res[1]*1000.0, res[2]*1000.0])
            return clamped, actual
        
        return clamped, None


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
