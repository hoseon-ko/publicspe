"""DeepAlign 백그라운드 워커 파일.

이 파일은 카메라 작업을 UI 스레드 밖에서 실행하기 위한 worker 객체를 정의합니다.
현재 포함된 worker는 다음과 같습니다.
- SNAP
- LIVE 폴링/스트림 루프
- ACQUIRE 프레임 시퀀스 및 진행률 콜백
- 프레임 RGB 변환 (numpy 연산 전담)
"""

from __future__ import annotations

import time

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, Qt


def _convert_raw_to_rgb(raw, cmap: str, vmin: float, vmax: float) -> np.ndarray:
    """numpy 변환 전용 함수 — Qt 접근 없음, 어느 스레드에서도 호출 가능."""
    arr = np.asarray(raw)

    if arr.ndim == 3 and arr.shape[2] == 3:
        if arr.dtype == np.uint8:
            return arr
        return np.clip(arr, 0, 255).astype(np.uint8)

    if arr.ndim != 2:
        arr = np.asarray(arr).squeeze()
        if arr.ndim != 2:
            arr = np.zeros((32, 32), dtype=np.uint8)

    if cmap and str(cmap).lower() != "off":
        from ui.colormap_utils import apply_colormap
        rgba = apply_colormap(arr.astype(np.float32), str(cmap), vmin=vmin, vmax=vmax)
        return np.ascontiguousarray(rgba[:, :, :3]).astype(np.uint8)

    _vmin = float(vmin) if vmin is not None else float(np.min(arr))
    _vmax = float(vmax) if vmax is not None else float(np.max(arr))
    if _vmax <= _vmin:
        gray = np.zeros_like(arr, dtype=np.uint8)
    else:
        scale = 255.0 / (_vmax - _vmin)
        gray = np.clip((arr.astype(np.float32) - _vmin) * scale, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(np.stack([gray, gray, gray], axis=-1))


class _FrameConvertWorker(QObject):
    """raw numpy → RGB 변환을 워커 스레드에서 수행.

    submit()은 메인 스레드에서 호출한다.
    QueuedConnection으로 _process()가 워커 스레드 이벤트 루프에서 실행됨.
    변환 완료 후 result_ready 시그널을 emit — Qt가 자동으로 수신측 스레드(메인)로 큐잉.
    """

    _submit = pyqtSignal(object)
    result_ready = pyqtSignal(object, object, str)  # rgb, raw, gallery_label

    def __init__(self):
        super().__init__()
        self._busy = False
        # moveToThread 전에 연결해도 됨 — 수신측 thread affinity는 deliver 시점에 적용
        self._submit.connect(self._process, Qt.ConnectionType.QueuedConnection)

    @property
    def busy(self) -> bool:
        return self._busy

    def submit(self, task: dict) -> None:
        """메인 스레드에서 호출 — QueuedConnection으로 워커 스레드에 전달."""
        self._submit.emit(task)

    @pyqtSlot(object)
    def _process(self, task: dict) -> None:
        self._busy = True
        try:
            rgb = _convert_raw_to_rgb(
                task["raw"], task["cmap"], task["vmin"], task["vmax"]
            )
            self.result_ready.emit(rgb, task["raw"], task.get("gallery_label", ""))
        except Exception:
            pass
        finally:
            self._busy = False


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


class _BgCaptureWorker(QObject):
    """N프레임 snap → 평균 → 배경 프레임 반환."""

    progress = pyqtSignal(int, int)   # current, total
    finished = pyqtSignal(object)     # averaged np.ndarray (원본 dtype)
    error    = pyqtSignal(str)

    def __init__(self, snap_fn, n_frames: int):
        super().__init__()
        self._snap_fn = snap_fn
        self._n = max(1, int(n_frames))
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        accum = None
        orig_dtype = None
        try:
            for i in range(self._n):
                if self._stop:
                    break
                frame = np.asarray(self._snap_fn())
                if orig_dtype is None:
                    orig_dtype = frame.dtype
                if accum is None:
                    accum = frame.astype(np.float64)
                else:
                    accum += frame.astype(np.float64)
                self.progress.emit(i + 1, self._n)
            if accum is not None:
                avg = (accum / max(1, self._n))
                if orig_dtype is not None and np.issubdtype(orig_dtype, np.integer):
                    info = np.iinfo(orig_dtype)
                    avg = np.clip(avg, info.min, info.max)
                self.finished.emit(avg.astype(orig_dtype if orig_dtype is not None else np.uint16))
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