"""
file_list_panel.py
열린 SPE 파일 목록 패널
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel,
    QPushButton, QSlider, QAbstractItemView, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class SpeFileItem:
    def __init__(self, filepath: str, spe_obj, num_frames: int = 1):
        self.filepath = filepath
        self.spe_obj = spe_obj
        self.num_frames = num_frames
        self.current_frame = 0

    @property
    def filename(self) -> str:
        return os.path.basename(self.filepath)

    @property
    def dirname(self) -> str:
        return os.path.dirname(self.filepath)


class FileListPanel(QWidget):
    file_selected = pyqtSignal(object, int)
    file_removed  = pyqtSignal(str)
    frame_changed = pyqtSignal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[SpeFileItem] = []
        self._current_item: SpeFileItem | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── Open 버튼 ──
        self.btn_open = QPushButton("📂  Open SPE")
        self.btn_open.setStyleSheet("""
            QPushButton {
                background-color: #0f3460;
                color: #e0e0e0;
                border: 1px solid #e94560;
                border-radius: 4px;
                padding: 6px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e94560;
                color: #ffffff;
            }
        """)
        layout.addWidget(self.btn_open)

        # ── 선택된 파일 정보 박스 ──
        self.info_box = QFrame()
        self.info_box.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border: 1px solid #0f3460;
                border-radius: 4px;
            }
        """)
        info_layout = QVBoxLayout(self.info_box)
        info_layout.setContentsMargins(8, 6, 8, 6)
        info_layout.setSpacing(2)

        self.lbl_filename = QLabel("—")
        self.lbl_filename.setStyleSheet(
            "color: #e0e0e0; font-size: 12px; font-weight: bold; border: none;"
        )
        self.lbl_filename.setWordWrap(True)

        self.lbl_dirpath = QLabel("")
        self.lbl_dirpath.setStyleSheet(
            "color: #606080; font-size: 10px; border: none;"
        )
        self.lbl_dirpath.setWordWrap(True)

        self.lbl_frames = QLabel("")
        self.lbl_frames.setStyleSheet(
            "color: #e94560; font-size: 10px; font-weight: bold; border: none;"
        )

        info_layout.addWidget(self.lbl_filename)
        info_layout.addWidget(self.lbl_dirpath)
        info_layout.addWidget(self.lbl_frames)
        layout.addWidget(self.info_box)

        # ── 구분선 ──
        layout.addWidget(self._make_line())

        # ── FILES 헤더 ──
        hdr_row = QHBoxLayout()
        hdr = QLabel("FILES")
        hdr.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: #e94560; letter-spacing: 2px;"
        )
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        self.btn_remove = QPushButton("✕ Remove")
        self.btn_remove.setEnabled(False)
        self.btn_remove.setFixedHeight(20)
        self.btn_remove.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #606080;
                border: 1px solid #0f3460;
                border-radius: 3px;
                font-size: 10px;
                padding: 0 6px;
            }
            QPushButton:hover { color: #e94560; border-color: #e94560; }
            QPushButton:disabled { color: #303050; border-color: #1a1a2e; }
        """)
        self.btn_remove.clicked.connect(self._on_remove_clicked)
        hdr_row.addWidget(self.btn_remove)
        layout.addLayout(hdr_row)

        # ── 파일 리스트 ──
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setStyleSheet("""
            QListWidget {
                background: #16213e;
                border: 1px solid #0f3460;
                border-radius: 4px;
                outline: none;
            }
            QListWidget::item {
                padding: 5px 8px;
                border-bottom: 1px solid #1a1a2e;
                color: #c0c0d0;
                font-size: 11px;
            }
            QListWidget::item:selected {
                background: #0f3460;
                color: #e94560;
                font-weight: bold;
            }
            QListWidget::item:hover { background: #0f3460; }
        """)
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list_widget)

        # ── 구분선 ──
        layout.addWidget(self._make_line())

        # ── 프레임 슬라이더 ──
        frame_hdr = QLabel("FRAME")
        frame_hdr.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: #e94560; letter-spacing: 2px;"
        )
        layout.addWidget(frame_hdr)

        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.setValue(0)
        self.frame_slider.setEnabled(False)
        self.frame_slider.valueChanged.connect(self._on_frame_changed)
        layout.addWidget(self.frame_slider)

        self.frame_label = QLabel("—")
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setStyleSheet(
            "color: #e94560; font-weight: bold; font-size: 11px;"
        )
        layout.addWidget(self.frame_label)
        layout.addStretch()

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def add_file(self, filepath: str, spe_obj, num_frames: int = 1):
        for item in self._items:
            if item.filepath == filepath:
                return

        spe_item = SpeFileItem(filepath, spe_obj, num_frames)
        self._items.append(spe_item)

        # 파일명만 표시 (확장자 제외)
        name = os.path.splitext(spe_item.filename)[0]
        frames_str = f"[{num_frames}f]" if num_frames > 1 else ""
        list_item = QListWidgetItem(f"{name}  {frames_str}")
        list_item.setData(Qt.ItemDataRole.UserRole, spe_item)
        list_item.setToolTip(filepath)
        self.list_widget.addItem(list_item)
        self.list_widget.setCurrentItem(list_item)

    def find_item(self, filepath: str):
        for item in self._items:
            if item.filepath == filepath:
                return item
        return None

    def select_file(self, filepath: str):
        for i in range(self.list_widget.count()):
            list_item = self.list_widget.item(i)
            spe_item = list_item.data(Qt.ItemDataRole.UserRole)
            if spe_item.filepath == filepath:
                self.list_widget.blockSignals(True)
                self.list_widget.setCurrentItem(list_item)
                self.list_widget.blockSignals(False)
                self._current_item = spe_item
                self._update_info_box(spe_item)
                break

    def set_frame(self, frame_idx: int):
        if self._current_item is None:
            return
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(frame_idx)
        self.frame_slider.blockSignals(False)
        self._current_item.current_frame = frame_idx
        total = self._current_item.num_frames
        self.frame_label.setText(f"{frame_idx + 1}  /  {total}")

    def clear_all(self):
        self._items.clear()
        self._current_item = None
        self.list_widget.clear()
        self._update_frame_controls(None)
        self._clear_info_box()

    # ─────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────

    def _on_item_changed(self, current, previous):
        if current is None:
            self._current_item = None
            self._update_frame_controls(None)
            self._clear_info_box()
            self.btn_remove.setEnabled(False)
            return

        spe_item: SpeFileItem = current.data(Qt.ItemDataRole.UserRole)
        self._current_item = spe_item
        self._update_frame_controls(spe_item)
        self._update_info_box(spe_item)
        self.btn_remove.setEnabled(True)
        self.file_selected.emit(spe_item, spe_item.current_frame)

    def _on_frame_changed(self, value: int):
        if self._current_item is None:
            return
        self._current_item.current_frame = value
        total = self._current_item.num_frames
        self.frame_label.setText(f"{value + 1}  /  {total}")
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
            self._clear_info_box()
            self.btn_remove.setEnabled(False)

    def _update_info_box(self, spe_item: SpeFileItem):
        name = os.path.splitext(spe_item.filename)[0]
        self.lbl_filename.setText(name)
        self.lbl_dirpath.setText(spe_item.dirname)
        self.lbl_frames.setText(
            f"Frames: {spe_item.num_frames}  |  Current: {spe_item.current_frame + 1}"
        )

    def _clear_info_box(self):
        self.lbl_filename.setText("—")
        self.lbl_dirpath.setText("")
        self.lbl_frames.setText("")

    def _update_frame_controls(self, spe_item: SpeFileItem | None):
        if spe_item is None or spe_item.num_frames <= 1:
            self.frame_slider.setEnabled(False)
            self.frame_slider.setMaximum(0)
            self.frame_slider.setValue(0)
            self.frame_label.setText("—")
        else:
            self.frame_slider.setEnabled(True)
            self.frame_slider.setMaximum(spe_item.num_frames - 1)
            self.frame_slider.setValue(spe_item.current_frame)
            self.frame_label.setText(
                f"{spe_item.current_frame + 1}  /  {spe_item.num_frames}"
            )

    def _make_line(self) -> QLabel:
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #0f3460;")
        return line