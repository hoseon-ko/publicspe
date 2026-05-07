"""
core/background_manager.py
배경(BG) 프레임 공유 싱글톤.

Live / Acquisition / Scan 탭이 동일한 BG를 사용하도록
단일 인스턴스에서 관리한다.

사용법:
    from core.background_manager import BackgroundManager

    bm = BackgroundManager.instance()
    bm.set_frame(raw)               # 단일 프레임 등록
    bm.bg_changed.connect(on_bg)    # 변경 알림
    result = bm.apply(frame)        # BG 차감 적용
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal


class BackgroundManager(QObject):
    """
    싱글톤 BG 관리자.

    bg_changed(bool) — BG 등록·해제 시 emit
                        True=BG 있음 / False=초기화
    """

    bg_changed = pyqtSignal(bool)   # has_bg

    _instance: BackgroundManager | None = None

    # ── 싱글톤 팩토리 ─────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> BackgroundManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 내부 ─────────────────────────────────────────────────────────
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: np.ndarray | None = None   # (N, H, W) float32

    # ── 프로퍼티 ────────────────────────────────────────────────────
    @property
    def has_bg(self) -> bool:
        return self._frames is not None

    @property
    def n_frames(self) -> int:
        return 0 if self._frames is None else int(self._frames.shape[0])

    @property
    def shape(self) -> tuple | None:
        """(H, W) 또는 None"""
        return None if self._frames is None else self._frames.shape[1:]

    # ── 등록 ────────────────────────────────────────────────────────
    def set_frame(self, frame: np.ndarray) -> None:
        """단일 프레임 BG 등록 (예: 라이브 SNAP)."""
        self._frames = frame.astype(np.float32)[np.newaxis]   # (1, H, W)
        self.bg_changed.emit(True)

    def set_frames(self, frames: np.ndarray) -> None:
        """복수 프레임 BG 등록 (예: SPE 파일 여러 프레임).
        frames: (N, H, W) or (H, W)
        """
        arr = np.asarray(frames, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[np.newaxis]
        self._frames = arr
        self.bg_changed.emit(True)

    def clear(self) -> None:
        """BG 초기화."""
        self._frames = None
        self.bg_changed.emit(False)

    # ── 적용 ────────────────────────────────────────────────────────
    def mean_frame(self) -> np.ndarray | None:
        """평균 BG 프레임 (float32). BG 없으면 None."""
        if self._frames is None:
            return None
        return self._frames.mean(axis=0)

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """BG 차감 후 원본 dtype으로 반환.
        BG 없거나 크기 불일치 시 원본 그대로 반환.
        """
        bg = self.mean_frame()
        if bg is None:
            return frame
        if bg.shape != frame.shape[:2] if frame.ndim == 2 else bg.shape != frame.shape[:2]:
            return frame
        result = frame.astype(np.float32) - bg
        np.clip(result, 0, None, out=result)
        return result.astype(frame.dtype)

    def apply_list(self, frames: list[np.ndarray]) -> list[np.ndarray]:
        """프레임 리스트 전체에 BG 차감 적용."""
        if not self.has_bg:
            return frames
        return [self.apply(f) for f in frames]

    # ── 정보 ────────────────────────────────────────────────────────
    def status_text(self) -> str:
        """UI 레이블용 상태 문자열."""
        if self._frames is None:
            return "BG: 없음"
        h, w = self._frames.shape[1], self._frames.shape[2]
        n = self._frames.shape[0]
        if n > 1:
            return f"BG: {w}×{h}  ({n}프레임)"
        return f"BG: {w}×{h}"
