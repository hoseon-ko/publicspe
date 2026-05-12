"""
roi_panel.py
ROI 리스트 패널 - Dock 위젯
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLabel, QPushButton, QAbstractItemView
)
from PyQt6.QtCore import pyqtSignal, Qt

# Active 강조 색상 (roi_items.py 와 동일)
COLOR_PROFILE_ACTIVE = '#ff2a4a'   # bright red
COLOR_HIST_ACTIVE    = '#00f5e4'   # bright teal

_STYLE_ACTIVE_PROFILE = (
    "background: rgba(255,42,74,0.13);"
    "border-left: 3px solid #ff2a4a;"
    "border-radius: 2px;"
)
_STYLE_ACTIVE_HIST = (
    "background: rgba(0,245,228,0.13);"
    "border-left: 3px solid #00f5e4;"
    "border-radius: 2px;"
)
_STYLE_NORMAL = "background: transparent; border: none;"


class RoiPanel(QWidget):
    roi_selected   = pyqtSignal(int)   # roi_id
    roi_deleted    = pyqtSignal(int)   # roi_id
    roi_goto       = pyqtSignal(int)   # roi_id (📍 버튼)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roi_ids: list[int] = []
        # roi_id → { 'widget', 'indicator', 'lbl', 'badge' }
        self._item_widgets: dict[int, dict] = {}
        self._active_profile_id: int | None = None
        self._active_hist_id:    int | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        header = QLabel("ROI LIST")
        header.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #e94560;"
            "letter-spacing: 2px;"
        )
        header_row.addWidget(header)
        header_row.addStretch()

        btn_clear_all = QPushButton("Clear All")
        btn_clear_all.setFixedHeight(22)
        btn_clear_all.setStyleSheet("""
            QPushButton {
                background: transparent; color: #606080;
                border: 1px solid #0f3460; border-radius: 3px;
                font-size: 10px; padding: 0 6px;
            }
            QPushButton:hover { color: #e94560; border-color: #e94560; }
        """)
        btn_clear_all.clicked.connect(self._on_clear_all)
        header_row.addWidget(btn_clear_all)
        layout.addLayout(header_row)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setStyleSheet("""
            QListWidget { background: #16213e; border: 1px solid #0f3460;
                          border-radius: 4px; outline: none; }
            QListWidget::item { padding: 4px 8px; border-bottom: 1px solid #1a1a2e;
                                color: #e0e0e0; font-size: 11px; }
            QListWidget::item:selected { background: #0f3460; color: #e94560; }
            QListWidget::item:hover { background: #0f3460; }
        """)
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self.list_widget)

        # Delete 키
        self.list_widget.keyPressEvent = self._list_key_press

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def add_roi(self, roi_id: int, label: str, color: str):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, roi_id)

        widget, refs = self._make_item_widget(roi_id, label, color)
        self._item_widgets[roi_id] = refs
        item.setSizeHint(widget.sizeHint())

        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)
        self._roi_ids.append(roi_id)
        self.list_widget.setCurrentItem(item)

    def remove_roi(self, roi_id: int):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == roi_id:
                self.list_widget.takeItem(i)
                break
        if roi_id in self._roi_ids:
            self._roi_ids.remove(roi_id)
        self._item_widgets.pop(roi_id, None)
        if self._active_profile_id == roi_id:
            self._active_profile_id = None
        if self._active_hist_id == roi_id:
            self._active_hist_id = None

    def update_label(self, roi_id: int, label: str):
        refs = self._item_widgets.get(roi_id)
        if refs:
            refs['lbl'].setText(label)
            return
        # fallback: findChild 방식
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == roi_id:
                widget = self.list_widget.itemWidget(item)
                if widget:
                    lbl = widget.findChild(QLabel)
                    if lbl:
                        lbl.setText(label)
                break

    def select_roi(self, roi_id: int):
        """외부에서 ROI 선택 (리스트 하이라이트)"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == roi_id:
                self.list_widget.blockSignals(True)
                self.list_widget.setCurrentItem(item)
                self.list_widget.blockSignals(False)
                break

    def clear_selection(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clearSelection()
        self.list_widget.setCurrentRow(-1)
        self.list_widget.blockSignals(False)

    def set_active_roi(self, roi_id: int, role: str):
        """
        role='profile' 또는 'hist' 로 지정한 ROI 아이템을 강조 표시.
        이전 같은 role의 active 아이템은 자동 해제.
        """
        if role == 'profile':
            # 기존 profile active 해제
            if self._active_profile_id is not None:
                self._reset_item_style(self._active_profile_id)
            self._active_profile_id = roi_id
            self._apply_item_style(roi_id, role)
        else:
            if self._active_hist_id is not None:
                self._reset_item_style(self._active_hist_id)
            self._active_hist_id = roi_id
            self._apply_item_style(roi_id, role)

    def clear_active_roi(self, role: str):
        """해당 role의 active 강조를 해제."""
        if role == 'profile' and self._active_profile_id is not None:
            self._reset_item_style(self._active_profile_id)
            self._active_profile_id = None
        elif role == 'hist' and self._active_hist_id is not None:
            self._reset_item_style(self._active_hist_id)
            self._active_hist_id = None

    def clear_all(self):
        self.list_widget.clear()
        self._roi_ids.clear()
        self._item_widgets.clear()
        self._active_profile_id = None
        self._active_hist_id    = None

    # ─────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────

    def _make_item_widget(self, roi_id: int, label: str, color: str):
        """위젯과 서브 위젯 레퍼런스 dict 반환."""
        widget = QWidget()
        widget.setStyleSheet(_STYLE_NORMAL)
        row = QHBoxLayout(widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)

        # 색상 인디케이터
        indicator = QLabel("●")
        indicator.setStyleSheet(f"color: {color}; font-size: 12px;")
        indicator.setFixedWidth(16)
        row.addWidget(indicator)

        # 라벨
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #e0e0e0; font-size: 11px;")
        row.addWidget(lbl)

        # Active 배지 (숨김 상태로 시작)
        badge = QLabel("")
        badge.setStyleSheet("font-family: 'Courier New'; font-size: 10px; font-weight: bold; padding: 0 3px;")
        badge.hide()
        row.addWidget(badge)

        row.addStretch()

        # 📍 버튼
        btn_goto = QPushButton("📍")
        btn_goto.setFixedSize(24, 22)
        btn_goto.setStyleSheet("""
            QPushButton { background: transparent; border: none; font-size: 12px; }
            QPushButton:hover { background: #0f3460; border-radius: 3px; }
        """)
        btn_goto.clicked.connect(lambda: self.roi_goto.emit(roi_id))
        row.addWidget(btn_goto)

        # 🗑 버튼
        btn_del = QPushButton("🗑")
        btn_del.setFixedSize(24, 22)
        btn_del.setStyleSheet("""
            QPushButton { background: transparent; border: none; font-size: 12px; }
            QPushButton:hover { background: #e94560; border-radius: 3px; }
        """)
        btn_del.clicked.connect(lambda: self.roi_deleted.emit(roi_id))
        row.addWidget(btn_del)

        refs = {
            'widget':    widget,
            'indicator': indicator,
            'lbl':       lbl,
            'badge':     badge,
            'orig_color': color,
        }
        return widget, refs

    def _apply_item_style(self, roi_id: int, role: str):
        refs = self._item_widgets.get(roi_id)
        if not refs:
            return
        if role == 'profile':
            color  = COLOR_PROFILE_ACTIVE
            style  = _STYLE_ACTIVE_PROFILE
            badge_text = "◆ PROFILE"
        else:
            color  = COLOR_HIST_ACTIVE
            style  = _STYLE_ACTIVE_HIST
            badge_text = "◆ HIST"

        refs['widget'].setStyleSheet(style)
        refs['indicator'].setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
        refs['lbl'].setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        refs['badge'].setText(badge_text)
        refs['badge'].setStyleSheet(
            f"color: {color}; font-family: 'Courier New'; font-size: 10px; font-weight: bold; padding: 0 3px;"
        )
        refs['badge'].show()

    def _reset_item_style(self, roi_id: int):
        refs = self._item_widgets.get(roi_id)
        if not refs:
            return
        refs['widget'].setStyleSheet(_STYLE_NORMAL)
        orig = refs.get('orig_color', '#e94560')
        refs['indicator'].setStyleSheet(f"color: {orig}; font-size: 12px;")
        refs['lbl'].setStyleSheet("color: #e0e0e0; font-size: 11px;")
        refs['badge'].hide()
        refs['badge'].setText("")

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        item = self.list_widget.item(row)
        if item:
            roi_id = item.data(Qt.ItemDataRole.UserRole)
            self.roi_selected.emit(roi_id)

    def _on_clear_all(self):
        for roi_id in list(self._roi_ids):
            self.roi_deleted.emit(roi_id)

    def _list_key_press(self, ev):
        if ev.key() == Qt.Key.Key_Delete:
            item = self.list_widget.currentItem()
            if item:
                roi_id = item.data(Qt.ItemDataRole.UserRole)
                self.roi_deleted.emit(roi_id)
        else:
            QListWidget.keyPressEvent(self.list_widget, ev)
