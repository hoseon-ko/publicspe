from __future__ import annotations
import numpy as np
import traceback
from PyQt6.QtGui import QImage, QPixmap
from ui.colormap_utils import apply_colormap # 기존 유틸리티 활용

class ImageProvider:
    """
    WPF의 ValueConverter와 유사한 역할.
    Raw 데이터를 뷰어 설정에 맞춰 시각화 가능한 QPixmap으로 변환합니다.
    """
    @staticmethod
    def convert(raw: np.ndarray, colormap: str = 'off', vmin=None, vmax=None) -> QPixmap:
        """단순 변환용 (Legacy/Fallback)"""
        return ImageProvider.get_display_pixmap(raw, colormap, vmin, vmax)

    @staticmethod
    def get_display_pixmap(raw: np.ndarray, colormap_name: str = 'off', 
                          vmin: float = 0, vmax: float = 255) -> QPixmap:
        """Raw 데이터를 설정된 vmin/vmax와 컬러맵을 적용하여 QPixmap으로 변환 (16비트 대응)"""
        try:
            if raw is None: return QPixmap()
            
            # 1. Normalization & Clipping
            data = raw.astype(np.float32)
            if vmin is None: vmin = float(np.min(data))
            if vmax is None: vmax = float(np.max(data))
            if vmax <= vmin: vmax = vmin + 1.0
            
            # 2. Apply Colormap
            if colormap_name in ('off', 'gray', 'grey'):
                # 그레이스케일 처리
                clipped = np.clip(data, vmin, vmax)
                data_8bit = ((clipped - vmin) / (vmax - vmin) * 255.0).astype(np.uint8)
                h, w = data_8bit.shape
                # 메모리 복사본 생성을 위해 .copy() 사용
                qimg = QImage(data_8bit.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
            else:
                # 전문 컬러맵 적용 (ui.colormap_utils 사용 - OpenCV 의존성 없음)
                rgba = apply_colormap(data, colormap_name, vmin=vmin, vmax=vmax)
                h, w = rgba.shape[:2]
                qimg = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
            
            return QPixmap.fromImage(qimg)
        except Exception as e:
            print(f"[ViewerV2:Error] get_display_pixmap failed: {e}")
            traceback.print_exc()
            return QPixmap()

    @staticmethod
    def get_point_profile(raw: np.ndarray, x: int, y: int) -> tuple[np.ndarray, np.ndarray]:
        """특정 픽셀 기준의 수평/수직 단면 추출"""
        h, w = raw.shape[:2]
        if 0 <= x < w and 0 <= y < h:
            return raw[y, :], raw[:, x]
        return np.array([]), np.array([])

    @staticmethod
    def get_line_profile(raw: np.ndarray, x0, y0, x1, y1) -> np.ndarray:
        """두 점 (x0, y0) ~ (x1, y1) 사이를 잇는 선분의 프로파일 추출"""
        h, w = raw.shape[:2]
        # 선분 길이 계산
        length = int(np.hypot(x1 - x0, y1 - y0))
        if length < 1:
            return np.array([])
            
        # 선분을 따라 좌표 샘플링
        x = np.linspace(x0, x1, length)
        y = np.linspace(y0, y1, length)
        
        # 유효 범위 제한
        valid = (x >= 0) & (x < w) & (y >= 0) & (y < h)
        x, y = x[valid], y[valid]
        
        if x.size == 0:
            return np.array([])
            
        # Nearest Neighbor 샘플링 (간단하고 빠름)
        return raw[y.astype(int), x.astype(int)]

    @staticmethod
    def get_roi_profile(raw: np.ndarray, x0, y0, x1, y1) -> tuple[np.ndarray, np.ndarray]:
        """
        ROI 영역을 기반으로 한 센스 있는 프로파일 생성:
        - X 프로파일: 박스 높이(iy0:iy1) 영역의 가로 평균 단면
        - Y 프로파일: 박스 너비(ix0:ix1) 영역의 세로 평균 단면
        """
        h, w = raw.shape[:2]
        ix0, ix1 = sorted([int(x0), int(x1)])
        iy0, iy1 = sorted([int(y0), int(y1)])
        
        ix0, ix1 = max(0, ix0), min(w, ix1)
        iy0, iy1 = max(0, iy0), min(h, iy1)
        
        # X 프로파일: 지정된 Y범위(박스 높이)의 데이터를 가로로 평균 (결과는 가로 폭 W)
        if iy1 > iy0:
            x_prof = np.mean(raw[iy0:iy1, :], axis=0)
        else:
            x_prof = np.array([])
            
        # Y 프로파일: 지정된 X범위(박스 너비)의 데이터를 세로로 평균 (결과는 세로 높이 H)
        if ix1 > ix0:
            y_prof = np.mean(raw[:, ix0:ix1], axis=1)
        else:
            y_prof = np.array([])
            
        return x_prof, y_prof
