"""
frame_grid_panel.py
썸네일 모드 / 리스트 모드 전환 가능한 프레임 패널
"""

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QGridLayout, QLabel, QCheckBox, QPushButton,
    QListWidget, QListWidgetItem, QAbstractItemView
)
from PyQt6.QtCore import pyqtSignal, Qt, QThread
from PyQt6.QtGui import QImage, QPixmap, QIcon


def _frame_to_rgb(frame: np.ndarray, size: int = 80):
    """numpy RGB 배열 생성 (스레드 안전 - QPixmap 생성 없음)"""
    try:
        f = frame.astype(np.float64)
        vmin, vmax = f.min(), f.max()
        f = (f - vmin) / (vmax - vmin) if vmax > vmin else np.zeros_like(f)

        r = np.clip(1.5 - np.abs(4.0 * f - 3.0), 0.0, 1.0)
        g = np.clip(1.5 - np.abs(4.0 * f - 2.0), 0.0, 1.0)
        b = np.clip(1.5 - np.abs(4.0 * f - 1.0), 0.0, 1.0)
        rgb = (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)

        h, w = rgb.shape[:2]
        scale = size / max(h, w)
        th, tw = max(1, int(h * scale)), max(1, int(w * scale))
        row_idx = np.linspace(0, h - 1, th).astype(int)
        col_idx = np.linspace(0, w - 1, tw).astype(int)
        return np.ascontiguousarray(rgb[np.ix_(row_idx, col_idx)])
    except Exception:
        return None


def _rgb_to_pixmap(rgb: np.ndarray) -> QPixmap:
    """numpy RGB 배열 → QPixmap (GUI 스레드에서만 호출)"""
    try:
        th, tw = rgb.shape[:2]
        img = QImage(rgb.data, tw, th, tw * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img.copy())
    except Exception:
        px = QPixmap(80, 80)
        px.fill(Qt.GlobalColor.darkGray)
        return px


def _frame_to_pixmap(frame: np.ndarray, size: int = 80) -> QPixmap:
    """하위 호환용 래퍼"""
    rgb = _frame_to_rgb(frame, size)
    if rgb is None:
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.darkGray)
        return px
    return _rgb_to_pixmap(rgb)


class ThumbnailWorker(QThread):
    """백그라운드에서 썸네일 RGB 배열 생성 후 시그널로 전달"""
    thumb_ready = pyqtSignal(str, int, str, object)  # filepath, frame_idx, label, rgb_ndarray

    def __init__(self, spe_obj, filepath: str, num_frames: int, filename: str, parent=None):
        super().__init__(parent)
        self._spe_obj = spe_obj
        self._filepath = filepath
        self._num_frames = num_frames
        self._filename = filename

    def run(self):
        for i in range(self._num_frames):
            frame = self._get_frame(self._spe_obj, i)
            if frame is None:
                continue
            label = self._filename if self._num_frames == 1 else f"{self._filename}_{i}"
            rgb = _frame_to_rgb(frame, size=80)
            self.thumb_ready.emit(self._filepath, i, label, rgb)

    def _get_frame(self, spe_obj, idx: int):
        try:
            if hasattr(spe_obj, 'frame'):
                return spe_obj.frame(idx)
            if hasattr(spe_obj, 'data'):
                data = spe_obj.data
                if isinstance(data, np.ndarray):
                    return data[idx] if data.ndim == 3 else data
            return None
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# 썸네일 아이템
# ─────────────────────────────────────────────────────────────────────────────

class FrameThumbItem(QWidget):
    toggled = pyqtSignal(str, int, bool)
    clicked = pyqtSignal(str, int)

    THUMB_W = 100
    THUMB_H = 145  # 썸네일 + 라벨 + 체크박스 충분히

    def __init__(self, filepath: str, frame_idx: int, label: str,
                 pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.frame_idx = frame_idx
        self._setup_ui(label, pixmap)

    def _setup_ui(self, label: str, pixmap: QPixmap):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.setFixedWidth(self.THUMB_W)
        self.setFixedHeight(self.THUMB_H)
        self._set_style(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # 썸네일
        self.thumb_label = QLabel()
        self.thumb_label.setPixmap(pixmap)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setFixedSize(88, 76)
        self.thumb_label.setScaledContents(True)
        layout.addWidget(self.thumb_label)

        # 라벨
        self.name_label = QLabel(label)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("color: #a0a0b0; font-size: 9px;")
        self.name_label.setWordWrap(True)
        self.name_label.setFixedHeight(28)
        layout.addWidget(self.name_label)

        # 체크박스
        cb_row = QHBoxLayout()
        cb_label = QLabel("✓")
        cb_label.setStyleSheet("color: #a0a0b0; font-size: 9px;")
        self.checkbox = QCheckBox()
        self.checkbox.setStyleSheet("QCheckBox::indicator { width: 14px; height: 14px; }")
        self.checkbox.toggled.connect(
            lambda checked: self.toggled.emit(self.filepath, self.frame_idx, checked)
        )
        cb_row.addStretch()
        cb_row.addWidget(cb_label)
        cb_row.addWidget(self.checkbox)
        cb_row.addStretch()
        layout.addLayout(cb_row)

    def set_selected(self, selected: bool):
        self._set_style(selected)
        color = "#e94560" if selected else "#a0a0b0"
        weight = "bold" if selected else "normal"
        self.name_label.setStyleSheet(f"color: {color}; font-size: 9px; font-weight: {weight};")

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
                FrameThumbItem:hover { border: 1px solid #e94560; }
            """)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def mousePressEvent(self, ev):
        self.clicked.emit(self.filepath, self.frame_idx)
        super().mousePressEvent(ev)


# ─────────────────────────────────────────────────────────────────────────────
# 메인 패널
# ─────────────────────────────────────────────────────────────────────────────

class FrameGridPanel(QWidget):
    frame_clicked = pyqtSignal(str, int)
    checked_frames_changed = pyqtSignal(list)

    COLS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_items: list[FrameThumbItem] = []
        self._thumb_map: dict[tuple, FrameThumbItem] = {}  # O(1) 조회용
        self._selected_key: tuple | None = None            # 현재 선택된 (filepath, frame_idx)
        self._thumb_workers: list[ThumbnailWorker] = []    # 실행 중인 워커 참조 유지
        self._list_data: list[tuple] = []
        self._mode = 'thumb'
        self._focused_idx = -1   # 키보드 포커스 인덱스
        self._setup_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── 헤더 + 모드 전환 버튼 ──
        header_row = QHBoxLayout()
        header = QLabel("FRAMES")
        header.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #e94560;"
            "letter-spacing: 2px; padding: 2px;"
        )
        header_row.addWidget(header)
        header_row.addStretch()

        self.btn_thumb = QPushButton("⊞ Grid")
        self.btn_list  = QPushButton("☰ List")

        STYLE_ACTIVE = """
            QPushButton {
                background-color: #e94560;
                color: #ffffff;
                border: 1px solid #e94560;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }
        """
        STYLE_INACTIVE = """
            QPushButton {
                background-color: transparent;
                color: #a0a0b0;
                border: 1px solid #0f3460;
                border-radius: 4px;
                font-size: 11px;
            }
            QPushButton:hover {
                border: 1px solid #e94560;
                color: #e0e0e0;
            }
        """
        self._style_active = STYLE_ACTIVE
        self._style_inactive = STYLE_INACTIVE

        for btn in (self.btn_thumb, self.btn_list):
            btn.setFixedHeight(22)
            btn.setFixedWidth(58)

        self.btn_thumb.setStyleSheet(STYLE_ACTIVE)
        self.btn_list.setStyleSheet(STYLE_INACTIVE)
        self.btn_thumb.clicked.connect(lambda: self._set_mode('thumb'))
        self.btn_list.clicked.connect(lambda: self._set_mode('list'))

        header_row.addWidget(self.btn_thumb)
        header_row.addWidget(self.btn_list)

        self.btn_clear_checks = QPushButton("Clear")
        self.btn_clear_checks.setFixedHeight(22)
        self.btn_clear_checks.setFixedWidth(46)
        self.btn_clear_checks.setToolTip("Clear all selections")
        self.btn_clear_checks.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #606080;
                border: 1px solid #0f3460;
                border-radius: 4px;
                font-size: 10px;
            }
            QPushButton:hover { color: #e94560; border-color: #e94560; }
        """)
        self.btn_clear_checks.clicked.connect(self._clear_all_checks)
        header_row.addWidget(self.btn_clear_checks)
        layout.addLayout(header_row)

        # ── 썸네일 그리드 뷰 ──
        self.scroll_thumb = QScrollArea()
        self.scroll_thumb.setWidgetResizable(True)
        self.scroll_thumb.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_thumb.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_thumb.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll_thumb.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scroll_thumb.installEventFilter(self)

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(6)
        self.grid_layout.setContentsMargins(4, 4, 4, 4)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll_thumb.setWidget(self.grid_container)
        layout.addWidget(self.scroll_thumb)

        # ── 리스트 뷰 ──
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setStyleSheet("""
            QListWidget { background: #16213e; border: none; outline: none; }
            QListWidget::item { padding: 4px 8px; border-bottom: 1px solid #1a1a2e; color: #e0e0e0; }
            QListWidget::item:selected { background: #0f3460; color: #e94560; }
            QListWidget::item:hover { background: #0f3460; }
        """)
        self.list_widget.itemClicked.connect(self._on_list_item_clicked)
        self.list_widget.itemChanged.connect(self._on_list_item_changed)
        self.list_widget.currentItemChanged.connect(self._on_list_current_changed)
        self.list_widget.installEventFilter(self)  # 리스트 스페이스바 체크/언체크
        self.list_widget.hide()
        layout.addWidget(self.list_widget)

    # ─────────────────────────────────────────
    # 모드 전환
    # ─────────────────────────────────────────

    def _clear_all_checks(self):
        """모든 체크박스 해제"""
        for thumb in self._thumb_items:
            thumb.checkbox.blockSignals(True)
            thumb.checkbox.setChecked(False)
            thumb.checkbox.blockSignals(False)
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.list_widget.blockSignals(False)
        self.checked_frames_changed.emit([])

    def _set_mode(self, mode: str):
        self._mode = mode
        self.btn_thumb.setStyleSheet(
            self._style_active if mode == 'thumb' else self._style_inactive
        )
        self.btn_list.setStyleSheet(
            self._style_active if mode == 'list' else self._style_inactive
        )
        if mode == 'thumb':
            self.scroll_thumb.show()
            self.list_widget.hide()
        else:
            self.scroll_thumb.hide()
            self.list_widget.show()
            self._sync_list_checks()  # 썸네일 체크 상태 → 리스트 동기화

    def _sync_list_checks(self):
        """썸네일 체크 상태를 리스트에 반영"""
        checked = {(t.filepath, t.frame_idx) for t in self._thumb_items if t.is_checked()}
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            fp, fi = item.data(Qt.ItemDataRole.UserRole)
            state = Qt.CheckState.Checked if (fp, fi) in checked else Qt.CheckState.Unchecked
            item.setCheckState(state)
        self.list_widget.blockSignals(False)

    def _sync_thumb_checks(self):
        """리스트 체크 상태를 썸네일에 반영"""
        checked = set()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                fp, fi = item.data(Qt.ItemDataRole.UserRole)
                checked.add((fp, fi))
        for thumb in self._thumb_items:
            thumb.checkbox.blockSignals(True)
            thumb.checkbox.setChecked((thumb.filepath, thumb.frame_idx) in checked)
            thumb.checkbox.blockSignals(False)

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def add_file(self, spe_obj, filepath: str, num_frames: int, filename: str):
        # 리스트 아이템 동기 추가 (텍스트만이라 빠름)
        for i in range(num_frames):
            label = filename if num_frames == 1 else f"{filename}_{i}"
            list_item = QListWidgetItem(f"  {label}")
            list_item.setData(Qt.ItemDataRole.UserRole, (filepath, i))
            list_item.setFlags(list_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            list_item.setCheckState(Qt.CheckState.Unchecked)
            self.list_widget.addItem(list_item)

        # 썸네일 비동기 생성 (백그라운드 스레드)
        worker = ThumbnailWorker(spe_obj, filepath, num_frames, filename)
        worker.thumb_ready.connect(self._on_thumb_ready)
        worker.finished.connect(
            lambda: self._thumb_workers.remove(worker) if worker in self._thumb_workers else None
        )
        self._thumb_workers.append(worker)
        worker.start()

    def _on_thumb_ready(self, filepath: str, frame_idx: int, label: str, rgb):
        """ThumbnailWorker 결과 수신 → GUI 스레드에서 QPixmap 생성 및 위젯 추가"""
        if rgb is not None:
            pixmap = _rgb_to_pixmap(rgb)
        else:
            pixmap = QPixmap(80, 80)
            pixmap.fill(Qt.GlobalColor.darkGray)

        thumb = FrameThumbItem(filepath, frame_idx, label, pixmap)
        thumb.clicked.connect(self._on_thumb_clicked)
        thumb.toggled.connect(self._on_thumb_toggled)
        total = len(self._thumb_items)
        row, col = divmod(total, self.COLS)
        self.grid_layout.addWidget(thumb, row, col)
        self._thumb_items.append(thumb)
        self._thumb_map[(filepath, frame_idx)] = thumb

        # 이미 선택된 키라면 선택 표시
        if self._selected_key == (filepath, frame_idx):
            thumb.set_selected(True)

    def remove_file(self, filepath: str):
        # 썸네일 제거
        to_remove = [t for t in self._thumb_items if t.filepath == filepath]
        for t in to_remove:
            key = (t.filepath, t.frame_idx)
            self.grid_layout.removeWidget(t)
            t.deleteLater()
            self._thumb_items.remove(t)
            self._thumb_map.pop(key, None)

        if self._selected_key and self._selected_key[0] == filepath:
            self._selected_key = None

        self._rebuild_grid()

        # 리스트에서 제거
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            fp, _ = item.data(Qt.ItemDataRole.UserRole)
            if fp == filepath:
                self.list_widget.takeItem(i)

    def set_current_frame(self, filepath: str, frame_idx: int):
        new_key = (filepath, frame_idx)
        if self._selected_key == new_key:
            return

        # 이전 선택만 해제 (전체 순회 없음 - O(1))
        if self._selected_key is not None:
            old_thumb = self._thumb_map.get(self._selected_key)
            if old_thumb is not None:
                old_thumb.set_selected(False)

        # 새 선택 적용
        new_thumb = self._thumb_map.get(new_key)
        if new_thumb is not None:
            new_thumb.set_selected(True)

        self._selected_key = new_key

        # 리스트 동기화
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            fp, fi = item.data(Qt.ItemDataRole.UserRole)
            if fp == filepath and fi == frame_idx:
                self.list_widget.blockSignals(True)
                self.list_widget.setCurrentItem(item)
                self.list_widget.blockSignals(False)
                break

    def get_checked_frames(self) -> list:
        return [
            (t.filepath, t.frame_idx)
            for t in self._thumb_items if t.is_checked()
        ]

    def clear(self):
        for w in self._thumb_workers:
            w.quit()
            w.wait()
        self._thumb_workers.clear()

        for item in self._thumb_items:
            self.grid_layout.removeWidget(item)
            item.deleteLater()
        self._thumb_items.clear()
        self._thumb_map.clear()
        self._selected_key = None
        self.list_widget.clear()

    # ─────────────────────────────────────────
    # 이벤트
    # ─────────────────────────────────────────

    def _on_thumb_clicked(self, filepath: str, frame_idx: int):
        for i, t in enumerate(self._thumb_items):
            if t.filepath == filepath and t.frame_idx == frame_idx:
                self._focused_idx = i
                break
        self.set_current_frame(filepath, frame_idx)
        self.frame_clicked.emit(filepath, frame_idx)
        self.scroll_thumb.setFocus()  # 키보드 포커스를 scroll_thumb 으로

    def _on_thumb_toggled(self, filepath: str, frame_idx: int, checked: bool):
        # 리스트 동기화
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            fp, fi = item.data(Qt.ItemDataRole.UserRole)
            if fp == filepath and fi == frame_idx:
                self.list_widget.blockSignals(True)
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                self.list_widget.blockSignals(False)
                break
        self.checked_frames_changed.emit(self.get_checked_frames())

    def _on_list_item_clicked(self, item: QListWidgetItem):
        fp, fi = item.data(Qt.ItemDataRole.UserRole)
        self.set_current_frame(fp, fi)
        self.frame_clicked.emit(fp, fi)

    def _on_list_current_changed(self, current: QListWidgetItem, previous):
        """키보드 이동 시 호출"""
        if current is None:
            return
        fp, fi = current.data(Qt.ItemDataRole.UserRole)
        self.set_current_frame(fp, fi)
        self.frame_clicked.emit(fp, fi)

    def _on_list_item_changed(self, item: QListWidgetItem):
        """리스트 체크박스 변경 → 썸네일 동기화"""
        fp, fi = item.data(Qt.ItemDataRole.UserRole)
        checked = item.checkState() == Qt.CheckState.Checked
        for thumb in self._thumb_items:
            if thumb.filepath == fp and thumb.frame_idx == fi:
                thumb.checkbox.blockSignals(True)
                thumb.checkbox.setChecked(checked)
                thumb.checkbox.blockSignals(False)
                break
        self.checked_frames_changed.emit(self.get_checked_frames())

    def _rebuild_grid(self):
        for i, item in enumerate(self._thumb_items):
            row, col = divmod(i, self.COLS)
            self.grid_layout.addWidget(item, row, col)

    def eventFilter(self, obj, ev):
        """키 이벤트 처리"""
        from PyQt6.QtCore import QEvent
        if ev.type() == QEvent.Type.KeyPress:
            # 그리드 모드 - 방향키 + 스페이스바
            if obj is self.scroll_thumb and self._mode == 'thumb':
                self.keyPressEvent(ev)
                return True
            # 리스트 모드 - 스페이스바로 체크/언체크
            if obj is self.list_widget and self._mode == 'list':
                if ev.key() == Qt.Key.Key_Space:
                    current = self.list_widget.currentItem()
                    if current is not None:
                        new_state = (
                            Qt.CheckState.Unchecked
                            if current.checkState() == Qt.CheckState.Checked
                            else Qt.CheckState.Checked
                        )
                        current.setCheckState(new_state)
                    return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        """그리드 모드 키보드 탐색"""
        if self._mode != 'thumb' or not self._thumb_items:
            super().keyPressEvent(ev)
            return

        key = ev.key()
        n = len(self._thumb_items)
        idx = max(self._focused_idx, 0)

        if key == Qt.Key.Key_Right:
            idx = min(idx + 1, n - 1)
        elif key == Qt.Key.Key_Left:
            idx = max(idx - 1, 0)
        elif key == Qt.Key.Key_Down:
            idx = min(idx + self.COLS, n - 1)
        elif key == Qt.Key.Key_Up:
            idx = max(idx - self.COLS, 0)
        elif key == Qt.Key.Key_Space:
            if 0 <= self._focused_idx < n:
                thumb = self._thumb_items[self._focused_idx]
                thumb.checkbox.setChecked(not thumb.checkbox.isChecked())
            return
        else:
            super().keyPressEvent(ev)
            return

        if 0 <= idx < n:
            self._focused_idx = idx
            thumb = self._thumb_items[idx]
            self.set_current_frame(thumb.filepath, thumb.frame_idx)
            self.frame_clicked.emit(thumb.filepath, thumb.frame_idx)
            self.scroll_thumb.ensureWidgetVisible(thumb)
            ev.accept()

    def _set_focused_idx(self, idx: int):
        self._focused_idx = idx

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