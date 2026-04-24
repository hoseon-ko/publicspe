"""
async_worker.py
비동기 SPE 로딩 워커 - QThread 기반
"""

from PyQt6.QtCore import QThread, pyqtSignal
import numpy as np


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
