"""KIMM Z 스캔 워커.

step별 메타데이터로 z 위치 + sharpness 결과를 dict 로 만들어 point_done 의
record 인자로 전달.
"""

from __future__ import annotations
from ui.deepalign.scan._scan_base import _ScanWorkerBase


class _KimmScanWorker(_ScanWorkerBase):
    """points: list[float] (Z µm absolute)."""
    _TAG = "KIMM-SCAN"

    def _make_step_record(self, point, result) -> dict:
        rec = {
            "scan_type": "kimm_z",
            "z_um": float(point),
        }
        if isinstance(result, dict):
            for k in ("sharpness",):
                if k in result:
                    rec[k] = result[k]
        return rec
