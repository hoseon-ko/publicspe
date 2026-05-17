"""Picomotor(Mirror) 스캔 워커."""

from __future__ import annotations
from ui.deepalign.scan._scan_base import _ScanWorkerBase


class _MirrorScanWorker(_ScanWorkerBase):
    """points: list[(motor_1based, target_steps_abs)]."""
    _TAG = "MIRROR-SCAN"
