"""
plot_panel.py
다중 프로파일 플롯 패널
체크박스로 선택된 프레임들의 프로파일을 겹쳐서 표시
"""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class PlotPanel(QWidget):
    def __init__(self, title: str = "Profile", parent=None):
        super().__init__(parent)
        self._title = title
        self._plot_items = []
        self._colors = [
            '#e94560', '#4ecdc4', '#ffe66d', '#a8e6cf',
            '#ff8b94', '#c7ceea', '#ffd3b6', '#d4f1f4',
            '#f6e58d', '#ff9ff3'
        ]
        self._color_idx = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        self.title_label = QLabel(self._title)
        self.title_label.setStyleSheet(
            "color: #e94560; font-weight: bold; font-size: 11px; letter-spacing: 1px;"
        )
        header_row.addWidget(self.title_label)
        header_row.addStretch()

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setFixedWidth(60)
        self.btn_clear.clicked.connect(self.clear)
        header_row.addWidget(self.btn_clear)
        layout.addLayout(header_row)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#16213e')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend(offset=(10, 10))
        for axis in ('bottom', 'left'):
            self.plot_widget.getAxis(axis).setPen('#0f3460')
            self.plot_widget.getAxis(axis).setTextPen('#a0a0b0')
        layout.addWidget(self.plot_widget)

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def plot_line(self, data: np.ndarray, label: str = "", clear_first: bool = True):
        if clear_first:
            self.clear()
        self._add_line(data, label)

    def plot_line_overlay(self, data: np.ndarray, label: str = ""):
        """기존 플롯 위에 겹쳐서 추가 (체크박스 다중 선택용)"""
        self._add_line(data, label)

    def plot_two_lines(self, data1: np.ndarray, data2: np.ndarray,
                       label1: str = "X mean", label2: str = "Y mean",
                       clear_first: bool = True):
        if clear_first:
            self.clear()
        self._add_line(data1, label1)
        self._add_line(data2, label2)

    def plot_multi_frames(self, profiles: list, labels: list):
        """여러 프레임 프로파일 한번에 그리기"""
        self.clear()
        for data, label in zip(profiles, labels):
            self._add_line(data, label)

    def clear(self):
        self.plot_widget.clear()
        self._plot_items.clear()
        self._color_idx = 0
        # 범례 재생성
        self.plot_widget.addLegend(offset=(10, 10))

    def set_xlabel(self, label: str):
        self.plot_widget.setLabel('bottom', label)

    def set_ylabel(self, label: str):
        self.plot_widget.setLabel('left', label)

    def set_title(self, title: str):
        self.title_label.setText(title)

    # ─────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────

    def _add_line(self, data: np.ndarray, label: str):
        color = self._colors[self._color_idx % len(self._colors)]
        self._color_idx += 1
        x = np.arange(len(data))
        item = self.plot_widget.plot(
            x, data,
            pen=pg.mkPen(color, width=1.5),
            name=label or None
        )
        self._plot_items.append(item)
