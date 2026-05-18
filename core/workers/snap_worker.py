"""공용 SnapWorker — 단일 프레임 캡처를 백그라운드 스레드에서 실행.

생성자에 callable(`snap_fn`)을 받는다. 호출자는 hub 경로/직결 경로 어느 쪽이든
무관하게 `lambda: hub.snap(owner)` 또는 `camera.snap` 등을 넘겨 사용한다.

이전: ui/live/live_tab._SnapWorker(camera) + ui/deepalign/deepalign_workers._SnapWorker(snap_fn)
→ 본 모듈의 SnapWorker(snap_fn) 하나로 단일화.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import dev_logger


class SnapWorker(QObject):
    """snap_fn()을 1회 호출해 결과 프레임을 success 시그널로 emit."""

    success = pyqtSignal(object)   # np.ndarray
    error   = pyqtSignal(str)

    def __init__(self, snap_fn: Callable[[], object]):
        super().__init__()
        self._snap_fn = snap_fn

    def run(self):
        try:
            frame = self._snap_fn()
            self.success.emit(np.asarray(frame))
        except Exception as e:
            dev_logger.exception("[SnapWorker] snap failed")
            self.error.emit(str(e))
