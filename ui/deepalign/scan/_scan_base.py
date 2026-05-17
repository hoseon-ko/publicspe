"""스캔 워커 공용 베이스.

Mover → Settle → Snap(avg) → process_fn → emit 의 단일 루프 알고리즘.
하위 워커(_MirrorScanWorker/_KimmScanWorker/_AcsScanWorker)는 동일 run() 사용,
타입힌트와 디버그 prefix(_TAG)만 차별화.
"""

from __future__ import annotations

import time
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal


class _ScanWorkerBase(QObject):
    """Signals
    -------
    point_started(idx, total, point)
    point_done(idx, total, point, frame, result)
    progress(idx, total)
    finished(results)
    error(msg)
    log(msg)
    """

    point_started = pyqtSignal(int, int, object)
    point_done    = pyqtSignal(int, int, object, object, object)
    progress      = pyqtSignal(int, int)
    finished      = pyqtSignal(list)
    error         = pyqtSignal(str)
    log           = pyqtSignal(str)

    _TAG = "SCAN"

    def __init__(self, mover, snap_fn, points: list,
                 process_fn=None, settle_ms: int = 200, avg_frames: int = 1):
        super().__init__()
        self._mover = mover
        self._snap_fn = snap_fn
        self._points = list(points)
        self._process_fn = process_fn
        self._settle_ms = max(0, int(settle_ms))
        self._avg_frames = max(1, int(avg_frames))
        self._stop = False

    def stop(self):
        self._stop = True

    def _settle(self) -> None:
        if self._settle_ms <= 0:
            return
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) * 1000 < self._settle_ms:
            if self._stop:
                return
            time.sleep(0.005)

    def _snap_avg(self) -> np.ndarray:
        frames = []
        for _ in range(self._avg_frames):
            if self._stop:
                break
            frames.append(np.asarray(self._snap_fn(), dtype=np.float32))
        if not frames:
            raise RuntimeError("snap 실패 (frame 없음)")
        if len(frames) == 1:
            return frames[0]
        return np.mean(frames, axis=0)

    def run(self):
        results: list = []
        total = len(self._points)
        try:
            for idx, point in enumerate(self._points, 1):
                if self._stop:
                    self.log.emit(f"[{self._TAG}] 사용자 중단 (idx={idx})")
                    break

                self.point_started.emit(idx, total, point)
                try:
                    self._mover.move(point)
                except Exception as e:
                    self.error.emit(f"[{self._TAG}] move 실패 (idx={idx}): {e}")
                    return

                self._settle()
                if self._stop:
                    break

                try:
                    frame = self._snap_avg()
                except Exception as e:
                    self.error.emit(f"[{self._TAG}] snap 실패 (idx={idx}): {e}")
                    return

                result = None
                if self._process_fn is not None:
                    try:
                        result = self._process_fn(frame, point, idx)
                    except Exception as e:
                        self.log.emit(f"[{self._TAG}] process_fn 예외 (idx={idx}): {e}")

                results.append(result)
                self.point_done.emit(idx, total, point, frame, result)
                self.progress.emit(idx, total)

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(f"[{self._TAG}] 루프 예외: {e}")
