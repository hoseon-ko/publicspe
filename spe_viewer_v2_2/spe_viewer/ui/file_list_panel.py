"""
file_list_panel.py
열린 SPE 파일 목록 패널
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel,
    QPushButton, QSlider, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon


class SpeFileItem:
    """리스트에 표시할 SPE 파일 정보"""
    def __init__(self, filepath: str, spe_obj, num_frames: int = 1):
        self.filepath = filepath
        self.spe_obj = spe_obj
        self.num_frames = num_frames
        self.current_frame = 0

    @property
    def filename(self) -> str:
        import os
        return os.path.basename(self.filepath)


class FileListPanel(QWidget):
    """
    좌측 사이드 패널: 열린 SPE 파일 목록 + 프레임 슬라이더
    """

    # 시그널
    file_selected = pyqtSignal(object, int)   # (SpeFileItem, frame_index)
    file_removed = pyqtSignal(str)             # filepath
    frame_changed = pyqtSignal(object, int)   # (SpeFileItem, frame_index)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[SpeFileItem] = []
        self._current_item: SpeFileItem | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 헤더
        header = QLabel("OPEN FILES")
        header.setStyleSheet("""
            font-size: 11px;
            font-weight: bold;
            color: #e94560;
            letter-spacing: 2px;
            padding: 4px 2px;
        """)
        layout.addWidget(header)

        # 파일 리스트
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        self.list_widget.setMinimumHeight(120)
        layout.addWidget(self.list_widget)

        # 파일 제거 버튼
        btn_row = QHBoxLayout()
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self._on_remove_clicked)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_remove)
        layout.addLayout(btn_row)

        # 구분선
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #0f3460;")
        layout.addWidget(line)

        # 프레임 컨트롤 영역
        frame_header = QLabel("FRAME")
        frame_header.setStyleSheet("""
            font-size: 11px;
            font-weight: bold;
            color: #e94560;
            letter-spacing: 2px;
            padding: 4px 2px;
        """)
        layout.addWidget(frame_header)

        # 프레임 슬라이더
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.setValue(0)
        self.frame_slider.setEnabled(False)
        self.frame_slider.valueChanged.connect(self._on_frame_changed)
        layout.addWidget(self.frame_slider)

        # 프레임 표시 레이블
        self.frame_label = QLabel("Frame: -")
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setStyleSheet("color: #e94560; font-weight: bold;")
        layout.addWidget(self.frame_label)

        layout.addStretch()

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def add_file(self, filepath: str, spe_obj, num_frames: int = 1):
        """새 SPE 파일을 리스트에 추가"""
        # 중복 체크
        for item in self._items:
            if item.filepath == filepath:
                return

        spe_item = SpeFileItem(filepath, spe_obj, num_frames)
        self._items.append(spe_item)

        display_text = f"{spe_item.filename}  [{num_frames}f]"
        list_item = QListWidgetItem(display_text)
        list_item.setData(Qt.ItemDataRole.UserRole, spe_item)
        list_item.setToolTip(filepath)
        self.list_widget.addItem(list_item)

        # 추가된 파일 자동 선택
        self.list_widget.setCurrentItem(list_item)

    def find_item(self, filepath: str):
        """filepath로 SpeFileItem 찾기"""
        for item in self._items:
            if item.filepath == filepath:
                return item
        return None

    def select_file(self, filepath: str):
        """filepath에 해당하는 리스트 항목 선택"""
        for i in range(self.list_widget.count()):
            list_item = self.list_widget.item(i)
            spe_item = list_item.data(Qt.ItemDataRole.UserRole)
            if spe_item.filepath == filepath:
                self.list_widget.blockSignals(True)
                self.list_widget.setCurrentItem(list_item)
                self.list_widget.blockSignals(False)
                self._current_item = spe_item
                break

    def set_frame(self, frame_idx: int):
        """그리드 클릭 시 슬라이더 동기화"""
        if self._current_item is None:
            return
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(frame_idx)
        self.frame_slider.blockSignals(False)
        self._current_item.current_frame = frame_idx
        total = self._current_item.num_frames
        self.frame_label.setText(f"Frame: {frame_idx + 1} / {total}")

    def clear_all(self):
        self._items.clear()
        self._current_item = None
        self.list_widget.clear()
        self._update_frame_controls(None)

    # ─────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────

    def _on_item_changed(self, current, previous):
        if current is None:
            self._current_item = None
            self._update_frame_controls(None)
            self.btn_remove.setEnabled(False)
            return

        spe_item: SpeFileItem = current.data(Qt.ItemDataRole.UserRole)
        self._current_item = spe_item
        self._update_frame_controls(spe_item)
        self.btn_remove.setEnabled(True)
        self.file_selected.emit(spe_item, spe_item.current_frame)

    def _on_frame_changed(self, value: int):
        if self._current_item is None:
            return
        self._current_item.current_frame = value
        total = self._current_item.num_frames
        self.frame_label.setText(f"Frame: {value + 1} / {total}")
        self.frame_changed.emit(self._current_item, value)

    def _on_remove_clicked(self):
        current = self.list_widget.currentItem()
        if current is None:
            return
        spe_item: SpeFileItem = current.data(Qt.ItemDataRole.UserRole)
        self._items.remove(spe_item)
        self.list_widget.takeItem(self.list_widget.row(current))
        self.file_removed.emit(spe_item.filepath)

        if not self._items:
            self._current_item = None
            self._update_frame_controls(None)
            self.btn_remove.setEnabled(False)

    def _update_frame_controls(self, spe_item: SpeFileItem | None):
        if spe_item is None or spe_item.num_frames <= 1:
            self.frame_slider.setEnabled(False)
            self.frame_slider.setMaximum(0)
            self.frame_slider.setValue(0)
            self.frame_label.setText("Frame: -")
        else:
            self.frame_slider.setEnabled(True)
            self.frame_slider.setMaximum(spe_item.num_frames - 1)
            self.frame_slider.setValue(spe_item.current_frame)
            self.frame_label.setText(
                f"Frame: {spe_item.current_frame + 1} / {spe_item.num_frames}"
            )
