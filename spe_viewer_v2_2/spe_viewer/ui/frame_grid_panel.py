"""
frame_grid_panel.py
전체 파일의 모든 프레임을 하나의 그리드에 표시
파일 추가될 때마다 해당 파일 프레임들을 그리드에 append
"""

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea,
    QGridLayout, QLabel, QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QImage, QPixmap


def _frame_to_pixmap(frame: np.ndarray, size: int = 80) -> QPixmap:
    """numpy 2D 프레임 → QPixmap 썸네일 (정확한 jet 컬러맵)"""
    try:
        f = frame.astype(np.float64)
        vmin, vmax = f.min(), f.max()
        if vmax > vmin:
            f = (f - vmin) / (vmax - vmin)
        else:
            f = np.zeros_like(f)

        # 정확한 jet 컬러맵
        # blue:  0.0~0.375 올라감, 0.375~0.625 유지, 0.625~0.875 내려감
        # green: 0.125~0.375 올라감, 0.375~0.625 유지, 0.625~0.875 내려감
        # red:   0.375~0.625 올라감, 0.625~0.875 유지, 0.875~1.0  내려감
        r = np.clip(1.5 - np.abs(4.0 * f - 3.0), 0.0, 1.0)
        g = np.clip(1.5 - np.abs(4.0 * f - 2.0), 0.0, 1.0)
        b = np.clip(1.5 - np.abs(4.0 * f - 1.0), 0.0, 1.0)

        rgb = (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)

        # PIL 없이 numpy 슬라이싱으로 리사이즈
        h, w = rgb.shape[:2]
        # 썸네일 목표 크기 계산 (비율 유지)
        scale = size / max(h, w)
        th = max(1, int(h * scale))
        tw = max(1, int(w * scale))
        # 인덱스 기반 리사이즈
        row_idx = (np.linspace(0, h - 1, th)).astype(int)
        col_idx = (np.linspace(0, w - 1, tw)).astype(int)
        thumb = rgb[np.ix_(row_idx, col_idx)]

        # contiguous array 필요
        thumb = np.ascontiguousarray(thumb)
        img = QImage(thumb.data, tw, th, tw * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img.copy())  # copy로 데이터 소유권 확보

    except Exception as e:
        print(f"Thumbnail error: {e}")
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.darkGray)
        return px


class FrameThumbItem(QWidget):
    """프레임 썸네일 + 라벨 + 체크박스"""

    toggled = pyqtSignal(str, int, bool)   # (filepath, frame_idx, checked)
    clicked = pyqtSignal(str, int)         # (filepath, frame_idx)

    THUMB_W = 100
    THUMB_H = 130

    def __init__(self, filepath: str, frame_idx: int, label: str,
                 pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.frame_idx = frame_idx
        self._setup_ui(label, pixmap)

    def _setup_ui(self, label: str, pixmap: QPixmap):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.setFixedWidth(self.THUMB_W)
        self.setFixedHeight(self.THUMB_H)
        self._set_style(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 썸네일
        self.thumb_label = QLabel()
        self.thumb_label.setPixmap(pixmap)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setFixedSize(88, 72)
        self.thumb_label.setScaledContents(True)
        layout.addWidget(self.thumb_label)

        # 라벨
        self.name_label = QLabel(label)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("color: #a0a0b0; font-size: 9px;")
        self.name_label.setWordWrap(True)
        self.name_label.setFixedHeight(30)
        layout.addWidget(self.name_label)

        # 체크박스
        self.checkbox = QCheckBox()
        self.checkbox.toggled.connect(
            lambda checked: self.toggled.emit(self.filepath, self.frame_idx, checked)
        )
        layout.addWidget(self.checkbox, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_selected(self, selected: bool):
        self._set_style(selected)
        if selected:
            self.name_label.setStyleSheet(
                "color: #e94560; font-size: 9px; font-weight: bold;"
            )
        else:
            self.name_label.setStyleSheet("color: #a0a0b0; font-size: 9px;")

    def _set_style(self, selected: bool):
        if selected:
            self.setStyleSheet("""
                FrameThumbItem {
                    background-color: #0f3460;
                    border: 2px solid #e94560;
                    border-radius: 4px;
                }
            """)
        else:
            self.setStyleSheet("""
                FrameThumbItem {
                    background-color: #16213e;
                    border: 1px solid #0f3460;
                    border-radius: 4px;
                }
                FrameThumbItem:hover {
                    border: 1px solid #e94560;
                }
            """)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def mousePressEvent(self, ev):
        self.clicked.emit(self.filepath, self.frame_idx)
        super().mousePressEvent(ev)


class FrameGridPanel(QWidget):
    """
    열린 모든 파일의 모든 프레임을 하나의 그리드에 표시.
    파일1 프레임 0,1,2... → 파일2 프레임 0,1,2... 순서로 이어붙임.
    """

    frame_clicked = pyqtSignal(str, int)          # (filepath, frame_idx)
    checked_frames_changed = pyqtSignal(list)      # [(filepath, frame_idx), ...]

    COLS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_items: list[FrameThumbItem] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QLabel("FRAMES")
        header.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #e94560;"
            "letter-spacing: 2px; padding: 4px 2px;"
        )
        layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(6)
        self.grid_layout.setContentsMargins(4, 4, 4, 4)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.scroll.setWidget(self.grid_container)
        layout.addWidget(self.scroll)

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def add_file(self, spe_obj, filepath: str, num_frames: int, filename: str):
        """파일 추가 - 해당 파일의 프레임들을 그리드 끝에 append"""
        for i in range(num_frames):
            frame = self._get_frame(spe_obj, i)
            if frame is None:
                continue

            # 라벨: 프레임 1개면 filename, 여러 개면 filename_i
            label = filename if num_frames == 1 else f"{filename}_{i}"

            pixmap = _frame_to_pixmap(frame, size=80)
            thumb = FrameThumbItem(filepath, i, label, pixmap)
            thumb.clicked.connect(self._on_thumb_clicked)
            thumb.toggled.connect(self._on_thumb_toggled)

            total = len(self._thumb_items)
            row, col = divmod(total, self.COLS)
            self.grid_layout.addWidget(thumb, row, col)
            self._thumb_items.append(thumb)

    def remove_file(self, filepath: str):
        """파일 제거 - 해당 파일의 썸네일 모두 제거 후 그리드 재배치"""
        to_remove = [t for t in self._thumb_items if t.filepath == filepath]
        for t in to_remove:
            self.grid_layout.removeWidget(t)
            t.deleteLater()
            self._thumb_items.remove(t)

        # 그리드 재배치
        self._rebuild_grid()

    def set_current_frame(self, filepath: str, frame_idx: int):
        """현재 뷰어에 표시 중인 프레임 하이라이트"""
        for item in self._thumb_items:
            item.set_selected(
                item.filepath == filepath and item.frame_idx == frame_idx
            )

    def get_checked_frames(self) -> list:
        """체크된 (filepath, frame_idx) 리스트 반환"""
        return [
            (item.filepath, item.frame_idx)
            for item in self._thumb_items if item.is_checked()
        ]

    def clear(self):
        for item in self._thumb_items:
            self.grid_layout.removeWidget(item)
            item.deleteLater()
        self._thumb_items.clear()

    # ─────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────

    def _rebuild_grid(self):
        """썸네일 제거 후 그리드 위치 재배치"""
        for i, item in enumerate(self._thumb_items):
            row, col = divmod(i, self.COLS)
            self.grid_layout.addWidget(item, row, col)

    def _on_thumb_clicked(self, filepath: str, frame_idx: int):
        self.set_current_frame(filepath, frame_idx)
        self.frame_clicked.emit(filepath, frame_idx)

    def _on_thumb_toggled(self, filepath: str, frame_idx: int, checked: bool):
        self.checked_frames_changed.emit(self.get_checked_frames())

    def _get_frame(self, spe_obj, idx: int):
        try:
            if hasattr(spe_obj, 'frame'):
                return spe_obj.frame(idx)
            if hasattr(spe_obj, 'data'):
                data = spe_obj.data
                if isinstance(data, np.ndarray):
                    return data[idx] if data.ndim == 3 else data
            return None
        except Exception as e:
            print(f"Frame extract error: {e}")
            return None
