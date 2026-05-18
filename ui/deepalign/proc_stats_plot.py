"""DeepAlign 처리 통계 시계열 플롯.

Mode 1/2/3 결과의 mean/min/max 를 시간 순으로 누적해 라인 차트로 표시.
사용자가 체크박스로 활성화하고, 라디오로 어떤 이벤트에서 점을 찍을지 선택한다.

기능:
- Crosshair (vLine) + 우상단 코너 라벨로 현재 호버 위치 값 표시
- mean/min/max 각각 체크박스로 show/hide
- ring buffer (최근 500점)

API:
    plot = ProcStatsPlot()
    plot.add_point(source="snap", mode=1, mean=..., mn=..., mx=...)
    plot.clear()
"""

from __future__ import annotations

from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QRadioButton,
    QButtonGroup, QPushButton, QLabel, QFrame, QSplitter,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)


_MAX_POINTS = 500

SOURCE_SNAP = "snap"
SOURCE_LIVE = "live"
SOURCE_ACQ  = "acquire"

# 라인 색 (라벨 색과 일치)
_COL_MAX  = "#e94560"
_COL_MEAN = "#4ecdc4"
_COL_MIN  = "#ffe66d"


class _IntAxis(pg.AxisItem):
    """Sample # 는 정수만 — 0.5, 1.5 같은 분수 tick 제거."""

    def tickValues(self, minVal, maxVal, size):
        # 부모가 만드는 tick 후보 중 정수만 통과시키고 중복 제거
        ticks = super().tickValues(minVal, maxVal, size)
        out = []
        for spacing, vals in ticks:
            int_vals = sorted({int(round(v)) for v in vals
                               if abs(v - round(v)) < 1e-6})
            if int_vals:
                out.append((max(1, int(round(spacing))), int_vals))
        return out

    def tickStrings(self, values, scale, spacing):
        return [str(int(round(v))) for v in values]


class ProcStatsPlot(QWidget):
    """Mode 1/2/3 결과 통계 시계열 플롯."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf_mean = deque(maxlen=_MAX_POINTS)
        self._buf_min  = deque(maxlen=_MAX_POINTS)
        self._buf_max  = deque(maxlen=_MAX_POINTS)
        self._build_ui()
        self._install_hover()

    # ── UI ───────────────────────────────────────────────────────
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        # 컨트롤 줄
        row = QHBoxLayout()
        row.setSpacing(6)
        self.chk_enable = QCheckBox("Enable")
        self.chk_enable.setChecked(False)
        row.addWidget(self.chk_enable)

        row.addWidget(self._vsep())

        self.radio_snap = QRadioButton("SNAP")
        self.radio_live = QRadioButton("LIVE")
        self.radio_all  = QRadioButton("ALL")
        self.radio_snap.setChecked(True)
        self._grp = QButtonGroup(self)
        self._grp.addButton(self.radio_snap, 0)
        self._grp.addButton(self.radio_live, 1)
        self._grp.addButton(self.radio_all,  2)
        for rb in (self.radio_snap, self.radio_live, self.radio_all):
            row.addWidget(rb)

        row.addWidget(self._vsep())

        # 라인 가시성 토글 — mean/min/max
        self.chk_mean = self._mk_color_check("mean", _COL_MEAN, True)
        self.chk_min  = self._mk_color_check("min",  _COL_MIN,  True)
        self.chk_max  = self._mk_color_check("max",  _COL_MAX,  True)
        for c in (self.chk_mean, self.chk_min, self.chk_max):
            row.addWidget(c)

        row.addStretch()

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear)
        row.addWidget(self.btn_clear)

        v.addLayout(row)

        # pyqtgraph
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(axisItems={"bottom": _IntAxis(orientation="bottom")})
        self.plot.setBackground("#0d121d")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Sample #")
        self.plot.setLabel("left", "Value")

        self.curve_max  = self.plot.plot(pen=pg.mkPen(_COL_MAX,  width=2), name="max")
        self.curve_mean = self.plot.plot(pen=pg.mkPen(_COL_MEAN, width=2), name="mean")
        self.curve_min  = self.plot.plot(pen=pg.mkPen(_COL_MIN,  width=2), name="min")

        # 체크박스 ↔ 라인 visibility
        self.chk_mean.toggled.connect(self.curve_mean.setVisible)
        self.chk_min.toggled.connect(self.curve_min.setVisible)
        self.chk_max.toggled.connect(self.curve_max.setVisible)

        # ── 표 (그래프와 동일한 데이터, 헤더 클릭으로 정렬 가능) ────────
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "max", "mean", "min"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setStyleSheet("""
            QTableWidget {
                background: #0d121d; color: #cbd5e1; gridline-color: #1e293b;
                font-family: 'Consolas','monospace'; font-size: 10pt;
                alternate-background-color: #0f1729; border: 1px solid #1e293b;
            }
            QHeaderView::section {
                background: #1e293b; color: #94a3b8; padding: 3px 6px;
                border: none; border-right: 1px solid #0d121d;
                font-weight: bold;
            }
            QTableWidget::item:selected { background: #1e3a5f; color: #f1f5f9; }
        """)

        # 그래프 + 표를 splitter 로 묶어 비율 조정 가능
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.plot)
        split.addWidget(self.table)
        split.setSizes([300, 150])
        split.setChildrenCollapsible(False)
        v.addWidget(split, 1)

    def _mk_color_check(self, text: str, color: str, checked: bool) -> QCheckBox:
        c = QCheckBox(text)
        c.setChecked(checked)
        c.setStyleSheet(
            f"QCheckBox {{ color: {color}; font-weight: bold; }}"
            f"QCheckBox::indicator:checked {{ background: {color}; border: 1px solid {color}; }}"
        )
        return c

    def _vsep(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet("color: #1e293b;")
        return f

    # ── Crosshair + hover label ──────────────────────────────────
    def _install_hover(self):
        # 세로선
        self._vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#94a3b8", width=1, style=Qt.PenStyle.DashLine),
        )
        self._vline.setZValue(10)
        # pyrefly: ignore [unexpected-keyword]
        self.plot.addItem(self._vline, ignoreBounds=True)
        self._vline.hide()

        # 우상단 고정 라벨 (HTML)
        self._hover_label = pg.LabelItem(justify="right")
        self._hover_label.setParentItem(self.plot.getPlotItem().getViewBox())
        # 우상단 anchor: (1,0)
        self._hover_label.anchor(itemPos=(1, 0), parentPos=(1, 0), offset=(-8, 8))
        self._hover_label.setText("")

        # 마우스 이동 시그널 — SignalProxy 로 throttle (rate=30Hz)
        scene = self.plot.getPlotItem().scene()
        self._mouse_proxy = pg.SignalProxy(
            scene.sigMouseMoved, rateLimit=30, slot=self._on_mouse_moved
        )

    def _on_mouse_moved(self, evt):
        pos = evt[0]
        vb = self.plot.getPlotItem().getViewBox()
        if not self.plot.getPlotItem().sceneBoundingRect().contains(pos):
            self._vline.hide()
            self._hover_label.setText("")
            return
        if len(self._buf_mean) == 0:
            self._vline.hide()
            self._hover_label.setText("")
            return
        mouse_pt = vb.mapSceneToView(pos)
        n = len(self._buf_mean)
        idx = int(round(mouse_pt.x()))
        idx = max(0, min(n - 1, idx))

        self._vline.setPos(idx)
        self._vline.show()

        mean_v = self._buf_mean[idx]
        min_v  = self._buf_min[idx]
        max_v  = self._buf_max[idx]
        self._hover_label.setText(
            f"<div style='background:#0d121d; padding:4px 8px;"
            f" border:1px solid #1e293b; font-family:monospace; font-size:10pt;'>"
            f"<b style='color:#94a3b8;'>Sample {idx}</b><br>"
            f"<span style='color:{_COL_MAX};'>max  : {max_v:.4f}</span><br>"
            f"<span style='color:{_COL_MEAN};'>mean : {mean_v:.4f}</span><br>"
            f"<span style='color:{_COL_MIN};'>min  : {min_v:.4f}</span>"
            f"</div>"
        )

    # ── public API ───────────────────────────────────────────────
    def add_point(self, source: str, mode: int, mean: float, mn: float, mx: float) -> None:
        if not self.chk_enable.isChecked():
            return
        sel = self._grp.checkedId()
        if sel == 0 and source != SOURCE_SNAP:
            return
        if sel == 1 and source != SOURCE_LIVE:
            return
        try:
            mean_f = float(mean); min_f = float(mn); max_f = float(mx)
        except (TypeError, ValueError):
            return
        self._buf_mean.append(mean_f)
        self._buf_min.append(min_f)
        self._buf_max.append(max_f)
        self._redraw()
        self._sync_table()

    def clear(self) -> None:
        self._buf_mean.clear()
        self._buf_min.clear()
        self._buf_max.clear()
        self._redraw()
        self._hover_label.setText("")
        self._vline.hide()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setSortingEnabled(True)

    # ── internal ────────────────────────────────────────────────
    def _sync_table(self) -> None:
        """ring buffer 를 표와 동기화. 헤더 정렬 보존."""
        # 정렬 잠시 끄고 데이터 채운 뒤 재가동 (속도 + 정렬 안정성)
        was_sorted = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        try:
            n = len(self._buf_mean)
            self.table.setRowCount(n)
            for r in range(n):
                # ring buffer 의 r 번째 = sample # r
                vals = (r, self._buf_max[r], self._buf_mean[r], self._buf_min[r])
                for c, v in enumerate(vals):
                    item = QTableWidgetItem()
                    if c == 0:
                        item.setData(Qt.ItemDataRole.DisplayRole, int(v))
                    else:
                        item.setData(Qt.ItemDataRole.DisplayRole, float(v))
                        item.setText(f"{float(v):.4f}")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter) 
                    self.table.setItem(r, c, item)
        finally:
            self.table.setSortingEnabled(was_sorted)

    def _redraw(self) -> None:
        n = len(self._buf_mean)
        if n == 0:
            self.curve_max.setData([], [])
            self.curve_mean.setData([], [])
            self.curve_min.setData([], [])
            return
        x = np.arange(n)
        self.curve_max.setData(x, np.fromiter(self._buf_max, dtype=float))
        self.curve_mean.setData(x, np.fromiter(self._buf_mean, dtype=float))
        self.curve_min.setData(x, np.fromiter(self._buf_min, dtype=float))
