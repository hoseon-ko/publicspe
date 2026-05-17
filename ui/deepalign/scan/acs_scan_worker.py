"""ACS 6축 스캔 워커.

호출자가 mover.enable() / mover.disable()을 스캔 전후로 직접 호출해야 함.
"""

from __future__ import annotations
from ui.deepalign.scan._scan_base import _ScanWorkerBase


class _AcsScanWorker(_ScanWorkerBase):
    """points: list[np.ndarray(6,)] (absolute targets)."""
    _TAG = "ACS-SCAN"
