"""ACS 6축 스캔 워커.

호출자가 mover.enable() / mover.disable() 을 스캔 전후로 직접 호출해야 함.
step별 메타데이터로 6축 절대 위치 (Y1/Z1/X1/Z2/Y2/Z3) 를 dict 로 만들어
point_done 의 record 인자로 전달.
"""

from __future__ import annotations
import numpy as np

from ui.deepalign.scan._scan_base import _ScanWorkerBase


class _AcsScanWorker(_ScanWorkerBase):
    """points: list[np.ndarray(6,)] (absolute targets, mm)."""
    _TAG = "ACS-SCAN"

    _AXIS_NAMES = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]

    def _make_step_record(self, point, result) -> dict:
        try:
            vals = np.asarray(point, dtype=float).reshape(-1).tolist()
        except Exception:
            vals = []

        rec: dict = {"scan_type": "acs_6axis"}
        for i, name in enumerate(self._AXIS_NAMES):
            rec[name] = float(vals[i]) if i < len(vals) else None

        if isinstance(result, dict):
            rec.update(result)
        return rec
