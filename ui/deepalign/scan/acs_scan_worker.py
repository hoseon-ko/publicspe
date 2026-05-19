"""ACS 6축 스캔 워커.

호출자가 mover.enable() / mover.disable() 을 스캔 전후로 직접 호출해야 함.

point payload 는 (cal_pos, dof_dict) 튜플:
  - cal_pos: ndarray(6,) — 6 모터 절대 위치 (Y1/Z1/X1/Z2/Y2/Z3, mm)
  - dof_dict: {Tx,Ty,Tz,Rx,Ry,Rz} — 사용자가 의도한 DOF (mm / mrad)
record 에는 양쪽 모두 기록 — 모터 위치(실측 명령) + DOF(사용자 의도) 보존.
"""

from __future__ import annotations
import numpy as np

from ui.deepalign.scan._scan_base import _ScanWorkerBase


class _AcsScanWorker(_ScanWorkerBase):
    """points: list[(np.ndarray(6,), dict)] — (cal_pos, dof_dict).

    옛 호환: cal_pos ndarray 단독도 허용 (DOF 정보 없는 record).
    """
    _TAG = "ACS-SCAN"

    _AXIS_NAMES = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
    _DOF_NAMES  = ["Tx", "Ty", "Tz", "Rx", "Ry", "Rz"]

    def _make_step_record(self, point, result) -> dict:
        # point 가 (cal_pos, dof_dict) 튜플인지 단독 ndarray 인지 분기
        cal = None
        dof_dict = None
        if isinstance(point, tuple) and len(point) >= 1:
            cal = point[0]
            if len(point) >= 2 and isinstance(point[1], dict):
                dof_dict = point[1]
        else:
            cal = point

        try:
            vals = np.asarray(cal, dtype=float).reshape(-1).tolist()
        except Exception:
            vals = []

        rec: dict = {"scan_type": "acs_6axis"}
        # 6 모터 cal_pos (Y1/Z1/X1/Z2/Y2/Z3)
        for i, name in enumerate(self._AXIS_NAMES):
            rec[name] = float(vals[i]) if i < len(vals) else None
        # 6 DOF (Tx/Ty/Tz/Rx/Ry/Rz) — 사용자 의도
        for name in self._DOF_NAMES:
            rec[name] = float(dof_dict[name]) if (dof_dict and name in dof_dict) else None

        if isinstance(result, dict):
            rec.update(result)
        return rec
