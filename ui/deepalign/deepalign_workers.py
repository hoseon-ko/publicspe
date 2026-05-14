"""DeepAlign 백그라운드 워커 파일.

이 파일은 카메라 작업을 UI 스레드 밖에서 실행하기 위한 worker 객체를 정의합니다.
현재 포함된 worker는 다음과 같습니다.
- SNAP
- LIVE 폴링/스트림 루프
- ACQUIRE 프레임 시퀀스 및 진행률 콜백
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, pyqtSignal


class _AcquireWorker(QObject):
    """hub.acquire_with_progress()를 워커 스레드에서 실행하는 워커.

    hub 경로만 지원합니다. acquire_fn은 반드시 제공해야 합니다.
    """

    frame_started = pyqtSignal(int, int)      # frame_idx(1-based), total
    progress = pyqtSignal(int, int, object)   # cur, total, raw_frame
    finished = pyqtSignal(list)               # frames
    error = pyqtSignal(str)

    def __init__(self, nframes: int, acquire_fn):
        super().__init__()
        self._acquire_fn = acquire_fn
        self._nframes = max(1, int(nframes))
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        frames = []
        try:
            def _on_frame(idx: int, total: int, frame):
                self.frame_started.emit(idx, total)
                self.progress.emit(idx, total, frame)

            frames = self._acquire_fn(self._nframes, _on_frame, lambda: self._stop_requested)
            self.finished.emit(frames)
        except Exception as e:
            self.error.emit(str(e))


class _SnapWorker(QObject):
    success = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, snap_fn):
        super().__init__()
        self._snap_fn = snap_fn

    def run(self):
        try:
            frame = self._snap_fn()
            self.success.emit(frame)
        except Exception as e:
            self.error.emit(str(e))


class _LiveWorker(QObject):
    """워커 스레드에서 hub.snap() 반복 호출 (메인 스레드 블로킹 방지)"""

    frame_ready = pyqtSignal(object)  # raw frame
    error = pyqtSignal(str)

    def __init__(self, snap_fn):
        super().__init__()
        self._snap_fn = snap_fn
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        try:
            while self._running:
                try:
                    frame = self._snap_fn()
                    if self._running:
                        self.frame_ready.emit(frame)
                    time.sleep(0.001)
                except Exception as e:
                    if self._running:
                        self.error.emit(str(e))
                    time.sleep(0.01)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self._running = False