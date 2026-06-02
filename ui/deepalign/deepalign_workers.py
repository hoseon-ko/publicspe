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


from ui.deepalign.image_metrics import ImageMetrics
from core.logger import calc_logger
from ui.deepalign.roi_finder import extract_ring_pixels
from ui.deepalign._perf_probe import perf_tick  # [임시 계측]


class _FrameConvertWorker(QObject):
    """raw numpy → RGB 변환 및 프레임 프로세싱을 워커 스레드에서 수행.

    submit()은 메인 스레드에서 호출한다.
    QueuedConnection으로 _process()가 워커 스레드 이벤트 루프에서 실행됨.
    변환 완료 후 result_ready 시그널을 emit — Qt가 자동으로 수신측 스레드(메인)로 큐잉.
    """

    _submit = pyqtSignal(object)
    result_ready = pyqtSignal(dict)  # result dictionary

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
        _perf_t0 = time.perf_counter()  # [임시 계측]
        _perf_src = task.get("source", "live")  # [임시 계측]
        try:
            raw = task["raw"]
            if raw is None:
                return

            # 1. 배경 차감 (Background Subtraction)
            bg_enabled = task.get("bg_enabled", False)
            bg_frame = task.get("bg_frame", None)
            if bg_enabled and bg_frame is not None and bg_frame.shape == raw.shape:
                result = raw.astype(np.int32) - bg_frame.astype(np.int32)
                if np.issubdtype(raw.dtype, np.unsignedinteger):
                    result = np.clip(result, 0, np.iinfo(raw.dtype).max)
                raw_after_bg = result.astype(raw.dtype)
            else:
                raw_after_bg = raw

            # 2. 이미지 프로세싱 & 메트릭 계산 (Mode 1/2/3)
            proc_enabled = task.get("proc_enabled", False)
            proc_mode = task.get("proc_mode", 1)
            proc_region = task.get("proc_region", "full")
            proc_image = task.get("proc_image", None)
            proc_bg_mode = task.get("proc_bg_mode", "ring")
            bg_gap = task.get("bg_gap", 2)
            bg_thickness = task.get("bg_thickness", 10)
            sig_roi_rect = task.get("sig_roi_rect", None)
            bg_roi_rect = task.get("bg_roi_rect", None)

            processed_raw = raw_after_bg
            stats_dict = None

            if proc_enabled:
                # Mode 1/2 는 proc image 필수
                has_proc_image = proc_image is not None and proc_image.shape == raw_after_bg.shape
                
                # ROI mask 계산 (region == 'roi' 일 때만)
                roi_slice = None
                if proc_region == "roi" and sig_roi_rect is not None:
                    h, w = raw_after_bg.shape[:2]
                    x0, y0, x1, y1 = sig_roi_rect
                    x0 = max(0, min(w, int(round(min(x0, x1)))))
                    x1 = max(0, min(w, int(round(max(x0, x1)))))
                    y0 = max(0, min(h, int(round(min(y0, y1)))))
                    y1 = max(0, min(h, int(round(max(y0, y1)))))
                    if x1 > x0 and y1 > y0:
                        roi_slice = (slice(y0, y1), slice(x0, x1))

                orig_dtype = raw_after_bg.dtype
                raw_roi = raw_after_bg if roi_slice is None else raw_after_bg[roi_slice]
                img_roi = None
                if proc_image is not None:
                    img_roi = proc_image if roi_slice is None else proc_image[roi_slice]

                valid_mode = True
                if proc_mode in (1, 2) and not has_proc_image:
                    valid_mode = False

                if valid_mode:
                    if proc_mode == 1:
                        full = raw_after_bg.astype(np.float32) - proc_image.astype(np.float32)
                        sample = raw_roi.astype(np.float32) - img_roi.astype(np.float32)
                        if np.issubdtype(orig_dtype, np.unsignedinteger):
                            full = np.clip(full, 0, np.iinfo(orig_dtype).max)
                            sample = np.clip(sample, 0, np.iinfo(orig_dtype).max)
                        processed_raw = full.astype(orig_dtype)
                    elif proc_mode == 2:
                        denom = img_roi.astype(np.float32)
                        denom[denom == 0] = np.nan
                        sample = raw_roi.astype(np.float32) / denom
                        full = raw_after_bg.astype(np.float32) / np.where(proc_image == 0, np.nan, proc_image)
                        processed_raw = full.astype(np.float32)
                    elif proc_mode == 3:
                        processed_raw = raw_after_bg
                        sample = raw_roi
                    else:
                        valid_mode = False

                if valid_mode:
                    # BG 픽셀 추출
                    bg_pixels = None
                    if roi_slice is not None:
                        if proc_bg_mode == 'ring' and sig_roi_rect is not None:
                            try:
                                x0r, y0r, x1r, y1r = sig_roi_rect
                                sig_xywh = (
                                    int(min(x0r, x1r)), int(min(y0r, y1r)),
                                    int(abs(x1r - x0r)), int(abs(y1r - y0r)),
                                )
                                ring = extract_ring_pixels(raw_after_bg.astype(np.float64), sig_xywh,
                                                          gap=bg_gap, thickness=bg_thickness)
                                if ring is not None and ring.size > 0:
                                    bg_pixels = ring.reshape(1, -1)
                            except Exception:
                                pass
                        elif proc_bg_mode == 'manual' and bg_roi_rect is not None:
                            try:
                                bx0, by0, bx1, by1 = bg_roi_rect
                                h, w = raw_after_bg.shape[:2]
                                bx0i = max(0, int(round(min(bx0, bx1))))
                                bx1i = min(w, int(round(max(bx0, bx1))))
                                by0i = max(0, int(round(min(by0, by1))))
                                by1i = min(h, int(round(max(by0, by1))))
                                if bx1i > bx0i and by1i > by0i:
                                    bg_pixels = raw_after_bg[by0i:by1i, bx0i:bx1i].astype(np.float64)
                            except Exception:
                                pass

                    _perf_t_m0 = time.perf_counter()  # [임시 계측]
                    pitch_nm = task.get("pitch_nm", 72.0)
                    metrics = ImageMetrics(sample, bg_2d=bg_pixels, pitch_nm=pitch_nm)
                    stats_dict = metrics.to_dict(profile=(_perf_src == "live"))  # [임시 계측] profile
                    if _perf_src == "live":  # [임시 계측]
                        perf_tick("worker.metrics_total", (time.perf_counter() - _perf_t_m0) * 1000.0)
                    calc_logger.info(
                        f"Mode {proc_mode} [{proc_region}] | Opt1={stats_dict['opt1']:.4f}  Opt2={stats_dict['opt2']:.4f}  Opt3={stats_dict['opt3']:.4f}"
                    )

            _perf_t_proc = time.perf_counter()  # [임시 계측] bg+proc+stats 끝

            # 3. RGB 이미지 변환
            rgb = _convert_raw_to_rgb(
                processed_raw, task["cmap"], task["vmin"], task["vmax"]
            )

            if _perf_src == "live":  # [임시 계측]
                _perf_t_rgb = time.perf_counter()
                perf_tick("worker.proc(bg+proc+stat)", (_perf_t_proc - _perf_t0) * 1000.0)
                perf_tick("worker.rgb_convert", (_perf_t_rgb - _perf_t_proc) * 1000.0)
                perf_tick("worker.total", (_perf_t_rgb - _perf_t0) * 1000.0)

            self.result_ready.emit({
                "rgb": rgb,
                "raw_after_bg": raw_after_bg,
                "processed_raw": processed_raw,
                "stats_dict": stats_dict,
                "gallery_label": task.get("gallery_label", ""),
                "source": task.get("source", "live")
            })
        except Exception:
            import traceback
            traceback.print_exc()
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