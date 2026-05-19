"""스캔 워커 공용 베이스.

Mover → Settle → Snap(avg) → process_fn → emit 의 단일 루프 알고리즘.
하위 워커(_MirrorScanWorker/_KimmScanWorker/_AcsScanWorker)는 동일 run() 사용,
타입힌트와 디버그 prefix(_TAG)만 차별화.

사용자 직관성을 위해 각 phase 마다 `phase` 시그널 + 친숙한 `log` 메시지를
emit. UI 측은 phase 시그널로 LED 인디케이터를, log 로 상세 라인을 갱신.
"""

from __future__ import annotations

import time
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal


# Phase 식별자 — UI 의 PhaseIndicator 와 1:1 매핑
PHASE_MOVE    = "move"
PHASE_SETTLE  = "settle"
PHASE_SNAP    = "snap"
PHASE_COMPUTE = "compute"
PHASE_DONE    = "done"


class _ScanWorkerBase(QObject):
    """Signals
    -------
    point_started(idx, total, point)
    point_done(idx, total, point, frame, result)
    progress(idx, total)
    phase(idx, total, phase_name, detail)   # phase_name ∈ PHASE_*
    finished(results)
    error(msg)
    log(msg)
    """

    point_started = pyqtSignal(int, int, object)
    point_done    = pyqtSignal(int, int, object, object, object)
    progress      = pyqtSignal(int, int)
    phase         = pyqtSignal(int, int, str, str)
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

    def _snap_avg(self, idx: int, total: int) -> np.ndarray:
        frames = []
        for k in range(self._avg_frames):
            if self._stop:
                break
            if self._avg_frames > 1:
                self.phase.emit(idx, total, PHASE_SNAP,
                                f"snap {k + 1}/{self._avg_frames}")
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
                self.log.emit(f"🎯 [{self._TAG}] {idx}/{total} 포인트 시작 → {point}")

                # ── MOVE ───────────────────────────────────────────────
                self.phase.emit(idx, total, PHASE_MOVE, f"→ {point}")
                self.log.emit(f"🚙 [{self._TAG}] {idx}/{total} 이동 중")
                t_move0 = time.perf_counter()
                try:
                    self._mover.move(point)
                except Exception as e:
                    self.error.emit(f"[{self._TAG}] move 실패 (idx={idx}): {e}")
                    return
                dt_move_ms = (time.perf_counter() - t_move0) * 1000.0
                self.log.emit(
                    f"✅ [{self._TAG}] {idx}/{total} in-position ({dt_move_ms:.0f} ms)"
                )

                # ── SETTLE ─────────────────────────────────────────────
                if self._settle_ms > 0:
                    self.phase.emit(idx, total, PHASE_SETTLE,
                                    f"settle {self._settle_ms} ms")
                    self.log.emit(
                        f"⏸ [{self._TAG}] {idx}/{total} settle {self._settle_ms} ms"
                    )
                    self._settle()
                if self._stop:
                    break

                # ── SNAP ───────────────────────────────────────────────
                self.phase.emit(idx, total, PHASE_SNAP,
                                f"snap (avg {self._avg_frames})")
                self.log.emit(
                    f"📷 [{self._TAG}] {idx}/{total} snap "
                    f"(avg {self._avg_frames})"
                )
                t_snap0 = time.perf_counter()
                try:
                    frame = self._snap_avg(idx, total)
                except Exception as e:
                    self.error.emit(f"[{self._TAG}] snap 실패 (idx={idx}): {e}")
                    return
                dt_snap_ms = (time.perf_counter() - t_snap0) * 1000.0
                self.log.emit(
                    f"📷 [{self._TAG}] {idx}/{total} snap 완료 ({dt_snap_ms:.0f} ms)"
                )

                # ── COMPUTE ────────────────────────────────────────────
                result = None
                if self._process_fn is not None:
                    self.phase.emit(idx, total, PHASE_COMPUTE, "process")
                    self.log.emit(f"🔧 [{self._TAG}] {idx}/{total} 처리 중")
                    try:
                        result = self._process_fn(frame, point, idx)
                    except Exception as e:
                        self.log.emit(
                            f"⚠ [{self._TAG}] process_fn 예외 (idx={idx}): {e}"
                        )

                results.append(result)
                self.point_done.emit(idx, total, point, frame, result)
                self.progress.emit(idx, total)
                self.phase.emit(idx, total, PHASE_DONE, "")
                self.log.emit(f"✨ [{self._TAG}] {idx}/{total} 완료")

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(f"[{self._TAG}] 루프 예외: {e}")
