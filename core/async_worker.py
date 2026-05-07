from typing import Any, Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


class TempPollerThread(QThread):
    """온도를 주기적으로 백그라운드에서 읽어 temp_read 시그널로 전달.
    메인 스레드 블로킹 없이 SDK get_temperature() 를 호출한다."""
    temp_read = pyqtSignal(object, object, object)  # reading, setpoint, status

    def __init__(self, cam, interval_ms: int = 3000):
        super().__init__()
        self._cam = cam
        self._interval = interval_ms

    def run(self):
        while not self.isInterruptionRequested():
            try:
                reading, setpoint, status = self._cam.get_temperature()
                self.temp_read.emit(reading, setpoint, status)
            except Exception:
                pass
            # 100ms 단위로 쪼개어 빠른 인터럽트 반응
            for _ in range(max(1, self._interval // 100)):
                if self.isInterruptionRequested():
                    return
                self.msleep(100)

    def stop(self):
        self.requestInterruption()
        self.quit()
        self.wait(2000)


class ColorMapWorker(QThread):
    """
    컬러맵 변환을 백그라운드에서 처리하는 워커.
    입력: 이미지(ndarray), 컬러맵명(str)
    출력: 변환된 RGBA ndarray (colormap_applied)
    """
    colormap_applied = pyqtSignal(object)  # (rgba ndarray)

    def __init__(self, image: np.ndarray, cmap: str,
             vmin: Optional[float] = None, 
             vmax: Optional[float] = None,
             parent: Optional[Any] = None):
        super().__init__(parent)
        self.image = image
        self.cmap = cmap
        self.vmin = vmin
        self.vmax = vmax

    @staticmethod
    def _to_grayscale_rgba(img: np.ndarray) -> np.ndarray:
        """임의 dtype grayscale → uint8 RGBA (export/동기 경로용)."""
        if img.ndim == 2:
            f = img.astype(np.float64)
            vmin, vmax = f.min(), f.max()
            f = (f - vmin) / (vmax - vmin) if vmax > vmin else np.zeros_like(f)
            ch = (f * 255).astype(np.uint8)
            return np.stack([ch, ch, ch, np.full_like(ch, 255)], axis=-1)
        elif img.ndim == 3 and img.shape[2] == 3:
            h, w = img.shape[:2]
            mx = img.max()
            rgb8 = img if img.dtype == np.uint8 else (img / (mx if mx > 0 else 1) * 255).astype(np.uint8)
            return np.concatenate([rgb8, np.full((h, w, 1), 255, np.uint8)], axis=2)
        return img

    def run(self):
        try:
            if self.cmap == 'off':
                self.colormap_applied.emit(self._to_grayscale_rgba(self.image))
                return
            from ui.colormap_utils import apply_colormap
            rgba = apply_colormap(self.image, self.cmap,
                                  vmin=self.vmin, vmax=self.vmax)
            self.colormap_applied.emit(rgba)
        except Exception as e:
            print(f"[ColorMapWorker] Error: {e}")


class SpeLoadWorker(QThread):
    """
    SPE 파일을 백그라운드에서 로드하는 워커.
    로딩 완료 후 signal로 UI에 데이터 전달.
    """

    # 시그널 정의
    progress = pyqtSignal(int)              # 진행률 0~100
    frame_loaded = pyqtSignal(int, object)  # (frame_index, np.ndarray)
    finished = pyqtSignal(str, object)      # (filepath, spe_object)
    error = pyqtSignal(str)                 # 에러 메시지

    def __init__(self, filepath: str, spe_class, parent=None):
        """
        Parameters
        ----------
        filepath : str
            로드할 SPE 파일 경로
        spe_class : class
            기존 SPE 로딩 클래스 (외부에서 주입)
        """
        super().__init__(parent)
        self.filepath = filepath
        self.spe_class = spe_class
        self._abort = False

    def abort(self):
        """로딩 중단 요청"""
        self._abort = True

    def run(self):
        """백그라운드 스레드에서 실행"""
        try:
            self.progress.emit(10)

            # 기존 SPE 클래스로 로드
            spe = self.spe_class(self.filepath)

            self.progress.emit(50)

            if self._abort:
                return

            # 프레임 수 확인
            num_frames = self._get_frame_count(spe)
            self.progress.emit(80)

            # 첫 프레임 미리 emit
            first_frame = self._get_frame(spe, 0)
            if first_frame is not None:
                self.frame_loaded.emit(0, first_frame)

            self.progress.emit(100)
            self.finished.emit(self.filepath, spe)

        except Exception as e:
            self.error.emit(f"로드 실패: {self.filepath}\n{str(e)}")

    def _get_frame_count(self, spe) -> int:
        """SPE 객체에서 프레임 수 추출 - 실제 클래스에 맞게 수정 필요"""
        # 일반적인 SPE 클래스 인터페이스 예시
        # 실제 클래스에 맞게 아래를 수정하세요
        if hasattr(spe, 'num_frames'):
            return spe.num_frames
        elif hasattr(spe, 'getNumFrames'):
            return spe.getNumFrames()
        elif hasattr(spe, 'data') and hasattr(spe.data, '__len__'):
            return len(spe.data)
        return 1

    def _get_frame(self, spe, index: int) -> np.ndarray:
        """SPE 객체에서 특정 프레임 추출 - 실제 클래스에 맞게 수정 필요"""
        try:
            if hasattr(spe, 'data'):
                data = spe.data
                if isinstance(data, np.ndarray):
                    if data.ndim == 3:
                        return data[index]
                    return data
                elif hasattr(data, '__getitem__'):
                    return np.array(data[index])
            elif hasattr(spe, 'get_frame'):
                return spe.get_frame(index)
            elif hasattr(spe, '__getitem__'):
                return np.array(spe[index])
        except Exception:
            pass
        return None


class SpeFrameWorker(QThread):
    """
    이미 로드된 SPE에서 특정 프레임만 빠르게 가져오는 워커.
    프레임 슬라이더 이동 시 사용.
    """

    frame_ready = pyqtSignal(int, object)  # (frame_index, np.ndarray)
    error = pyqtSignal(str)

    def __init__(self, spe, frame_index: int, parent=None):
        super().__init__(parent)
        self.spe = spe
        self.frame_index = frame_index

    def run(self):
        try:
            if hasattr(self.spe, 'data'):
                data = self.spe.data
                if isinstance(data, np.ndarray):
                    if data.ndim == 3:
                        frame = data[self.frame_index]
                    else:
                        frame = data
                elif hasattr(data, '__getitem__'):
                    frame = np.array(data[self.frame_index])
                else:
                    frame = np.array(data)
            elif hasattr(self.spe, 'get_frame'):
                frame = self.spe.get_frame(self.frame_index)
            else:
                frame = np.array(self.spe[self.frame_index])

            self.frame_ready.emit(self.frame_index, frame)

        except Exception as e:
            self.error.emit(str(e))
