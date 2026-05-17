"""KIMM Z 스캔 워커."""

from __future__ import annotations
from ui.deepalign.scan._scan_base import _ScanWorkerBase


class _KimmScanWorker(_ScanWorkerBase):
    """points: list[float] (Z µm absolute)."""
    _TAG = "KIMM-SCAN"
