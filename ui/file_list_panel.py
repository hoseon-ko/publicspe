"""
file_list_panel.py
열린 SPE 파일 목록 패널
"""

import os
import xml.etree.ElementTree as ET
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel,
    QPushButton, QSlider, QAbstractItemView, QFrame,
    QDialog, QTextEdit, QTabWidget, QApplication,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush


_DIALOG_QSS = """
    QDialog {
        background: #1a1a2e;
        color: #e0e0e0;
    }
    QTabWidget::pane {
        border: 1px solid #0f3460;
        background: #16213e;
    }
    QTabBar::tab {
        background: #16213e;
        color: #a0a0b0;
        padding: 6px 18px;
        border: 1px solid #0f3460;
        font-size: 11px;
    }
    QTabBar::tab:selected {
        background: #0f3460;
        color: #e94560;
        font-weight: bold;
    }
    QTextEdit {
        background: #16213e;
        color: #d0d0e0;
        border: none;
        font-family: Consolas, monospace;
        font-size: 11px;
        selection-background-color: #e94560;
    }
    QTreeWidget {
        background: #16213e;
        color: #d0d0e0;
        border: none;
        font-family: Consolas, monospace;
        font-size: 11px;
        alternate-background-color: #1c2444;
        outline: none;
    }
    QTreeWidget::item {
        padding: 2px 4px;
        border: none;
    }
    QTreeWidget::item:selected {
        background: #0f3460;
        color: #e0e0e0;
    }
    QTreeWidget::item:hover {
        background: #0f3460;
    }
    QHeaderView::section {
        background: #0f3460;
        color: #e94560;
        font-weight: bold;
        font-size: 11px;
        padding: 4px;
        border: none;
        border-right: 1px solid #1a1a2e;
    }
    QLineEdit {
        background: #16213e;
        color: #e0e0e0;
        border: 1px solid #0f3460;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 11px;
        selection-background-color: #e94560;
    }
    QLineEdit:focus { border-color: #e94560; }
    QPushButton {
        background: #0f3460;
        color: #e0e0e0;
        border: 1px solid #e94560;
        border-radius: 4px;
        padding: 4px 14px;
        font-size: 11px;
    }
    QPushButton:hover { background: #e94560; color: #ffffff; }
    QScrollBar:vertical {
        background: #1a1a2e; width: 8px; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #0f3460; border-radius: 4px; min-height: 20px;
    }
    QScrollBar::handle:vertical:hover { background: #e94560; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class SpeInfoDialog(QDialog):
    """
    SPE 파일 메타정보 / XML 조회 다이얼로그.
    - [Metadata] 탭 : 파스된 key/value 표시
    - [XML Tree] 탭  : 계층형 트리 덼 (SPE 3.0 전용)
    """

    def __init__(self, spe_item, parent=None):
        super().__init__(parent)
        spe_obj  = spe_item.spe_obj
        filename = os.path.basename(spe_item.filepath)
        self.setWindowTitle(f"SPE Info — {filename}")
        self.resize(800, 620)
        self.setStyleSheet(_DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # ―― Metadata 탭 ――
        meta_text = QTextEdit()
        meta_text.setReadOnly(True)
        meta_text.setPlainText(self._format_meta(spe_obj))
        self._tabs.addTab(meta_text, "Metadata")

        # ―― XML Tree 탭 (SPE 3.0만) ――
        xml_str = getattr(spe_obj, '_xml', None)
        self._xml_str = xml_str
        self._tree_widget = None
        self._all_items: list[QTreeWidgetItem] = []
        if xml_str:
            xml_tab = self._build_xml_tab(xml_str)
            self._tabs.addTab(xml_tab, "XML Tree")

        # ―― 하단 버튼 ――
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_copy_meta = QPushButton("Copy Metadata")
        btn_copy_meta.clicked.connect(lambda: QApplication.clipboard().setText(meta_text.toPlainText()))
        btn_row.addWidget(btn_copy_meta)

        if xml_str:
            btn_copy_xml = QPushButton("Copy XML")
            btn_copy_xml.clicked.connect(self._copy_xml)
            btn_row.addWidget(btn_copy_xml)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    # ――――――――――――――――――――――――――――――――
    # XML Tree 탭 빌더
    # ――――――――――――――――――――――――――――――――

    def _build_xml_tab(self, xml_str: str) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 6, 0, 0)
        vbox.setSpacing(6)

        # 검색란
        search_row = QHBoxLayout()
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search tag / value...")
        self._search_box.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_box)

        btn_expand = QPushButton("Expand All")
        btn_expand.setFixedWidth(90)
        btn_expand.clicked.connect(lambda: self._tree_widget.expandAll())
        search_row.addWidget(btn_expand)

        btn_collapse = QPushButton("Collapse")
        btn_collapse.setFixedWidth(80)
        btn_collapse.clicked.connect(lambda: self._tree_widget.collapseAll())
        search_row.addWidget(btn_collapse)
        vbox.addLayout(search_row)

        # 트리 위젯
        self._tree_widget = QTreeWidget()
        self._tree_widget.setColumnCount(2)
        self._tree_widget.setHeaderLabels(["Element / Attribute", "Value"])
        self._tree_widget.setAlternatingRowColors(True)
        self._tree_widget.setColumnWidth(0, 340)
        self._tree_widget.setAnimated(True)
        self._tree_widget.header().setStretchLastSection(True)
        vbox.addWidget(self._tree_widget)

        try:
            root = ET.fromstring(xml_str.encode('utf-8'))
            top = QTreeWidgetItem(self._tree_widget)
            top.setText(0, self._tag(root))
            top.setForeground(0, QBrush(QColor("#e94560")))
            top.setFont(0, QFont("Consolas", 11, QFont.Weight.Bold))
            self._all_items.append(top)
            self._populate_tree(top, root)
            self._tree_widget.addTopLevelItem(top)
            top.setExpanded(True)
            # 첫 두 레벨만 펼치기
            for i in range(top.childCount()):
                top.child(i).setExpanded(True)
        except Exception as e:
            err = QTreeWidgetItem(self._tree_widget, ["Parse error", str(e)])

        # 검색 디바운싱 타이머
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._run_search)

        return container

    def _populate_tree(self, parent_item: QTreeWidgetItem, elem):
        # 속성은 하위 로우로
        ns_stripped = self._strip_ns
        for attr_name, attr_val in elem.attrib.items():
            attr_item = QTreeWidgetItem(parent_item)
            attr_item.setText(0, f"@{ns_stripped(attr_name)}")
            attr_item.setText(1, attr_val)
            attr_item.setForeground(0, QBrush(QColor("#7ec8e3")))
            self._all_items.append(attr_item)

        # 텍스트 콘텐츠
        text = (elem.text or "").strip()
        if text:
            parent_item.setText(1, text)
            parent_item.setForeground(1, QBrush(QColor("#a8e6a3")))

        # 자식 요소
        for child in elem:
            child_item = QTreeWidgetItem(parent_item)
            child_item.setText(0, self._tag(child))
            child_item.setForeground(0, QBrush(QColor("#c9b1ff")))
            self._all_items.append(child_item)
            self._populate_tree(child_item, child)

    @staticmethod
    def _tag(elem) -> str:
        tag = elem.tag
        if '}' in tag:
            tag = tag.split('}', 1)[1]
        return tag

    @staticmethod
    def _strip_ns(name: str) -> str:
        if '}' in name:
            return name.split('}', 1)[1]
        return name

    # ――――――――――――――――――――――――――――――――
    # 검색
    # ――――――――――――――――――――――――――――――――

    def _on_search_changed(self, text: str):
        self._search_timer.start()

    def _run_search(self):
        keyword = self._search_box.text().strip().lower()
        _HIGHLIGHT = QBrush(QColor("#e94560"))
        _NORMAL_TAG = QBrush(QColor("#c9b1ff"))
        _NORMAL_ATTR = QBrush(QColor("#7ec8e3"))
        _NORMAL_ROOT = QBrush(QColor("#e94560"))

        for item in self._all_items:
            col0 = item.text(0)
            col1 = item.text(1)
            match = keyword and (keyword in col0.lower() or keyword in col1.lower())

            if match:
                item.setBackground(0, _HIGHLIGHT)
                item.setBackground(1, _HIGHLIGHT)
                # 부모를 접으면서 스크롤도
                p = item.parent()
                while p:
                    p.setExpanded(True)
                    p = p.parent()
            else:
                item.setBackground(0, QBrush())
                item.setBackground(1, QBrush())

        if keyword:
            # 첫 번째 매치 항목으로 스크롤
            for item in self._all_items:
                if item.background(0).color() == QColor("#e94560"):
                    self._tree_widget.scrollToItem(item)
                    break

    # ――――――――――――――――――――――――――――――――
    # 유틸 / 복사
    # ――――――――――――――――――――――――――――――――

    def _copy_xml(self):
        if not self._xml_str:
            return
        try:
            import xml.dom.minidom
            dom = xml.dom.minidom.parseString(self._xml_str.encode('utf-8'))
            pretty = dom.toprettyxml(indent='  ')
        except Exception:
            pretty = self._xml_str
        QApplication.clipboard().setText(pretty)

    @staticmethod
    def _format_meta(spe_obj) -> str:
        import numpy as np
        meta = getattr(spe_obj, 'meta', {})
        lines = []
        for k, v in meta.items():
            if k == 'wavelengths_nm' and isinstance(v, np.ndarray):
                if len(v) > 8:
                    preview = ', '.join(f"{x:.4f}" for x in v[:4])
                    tail    = ', '.join(f"{x:.4f}" for x in v[-4:])
                    lines.append(f"{'wavelengths_nm':<32}  [{preview}, ..., {tail}]  ({len(v)} pts)")
                else:
                    lines.append(f"{k:<32}  {v}")
            elif v is None or v == '' or v == []:
                lines.append(f"{k:<32}  —")
            else:
                lines.append(f"{k:<32}  {v}")
        return '\n'.join(lines) if lines else '(no metadata)'


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
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
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

    def _on_item_double_clicked(self, item: QListWidgetItem):
        spe_item: SpeFileItem = item.data(Qt.ItemDataRole.UserRole)
        if spe_item is not None:
            dlg = SpeInfoDialog(spe_item, parent=self)
            dlg.exec()

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