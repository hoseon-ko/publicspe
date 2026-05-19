"""Picomotor(Mirror) 스캔 워커.

step별 메타데이터로 M1-M4 위치 + centroid 분석 결과를 dict 로 만들어
point_done(.., record) 으로 전달. main_tab 이 SPE/CSV 에 사용.
"""

from __future__ import annotations
from ui.deepalign.scan._scan_base import _ScanWorkerBase


class _MirrorScanWorker(_ScanWorkerBase):
    """points: list[(motor_1based, target_steps_abs)]."""
    _TAG = "MIRROR-SCAN"

    def __init__(self, mover, snap_fn, points, *,
                 session_hub=None, process_fn=None,
                 settle_ms: int = 200, avg_frames: int = 1):
        super().__init__(mover, snap_fn, points,
                         process_fn=process_fn,
                         settle_ms=settle_ms, avg_frames=avg_frames)
        # hub 는 M1-M4 위치 조회용. None 이면 record 에 위치 누락.
        self._hub = session_hub

    def _make_step_record(self, point, result) -> dict:
        motor, target = (int(point[0]), int(point[1])) if isinstance(point, (tuple, list)) else (0, 0)

        # 4축 모두 위치 조회 — 이동 안 한 축도 기록 (정적 위치)
        positions = [None, None, None, None]
        if self._hub is not None:
            try:
                for axis in range(1, 5):
                    positions[axis - 1] = int(self._hub.pico_get_position(axis))
            except Exception:
                pass

        rec = {
            "scan_type": "mirror",
            "moved_motor": motor,
            "target_steps": target,
            "M1": positions[0], "M2": positions[1],
            "M3": positions[2], "M4": positions[3],
        }
        if isinstance(result, dict):
            for k in ("cent_x", "cent_y", "sigma_x", "sigma_y", "snr"):
                if k in result:
                    rec[k] = result[k]
        return rec
