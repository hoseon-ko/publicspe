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
    QButtonGroup, QPushButton, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QFileDialog, QMessageBox
)


_MAX_POINTS = 500

SOURCE_SNAP = "snap"
SOURCE_LIVE = "live"
SOURCE_ACQ  = "acquire"

# 동적 옵션 정의: (표시명, 키, 색상, 기본활성화여부)
# 밝기 8종 (opt1~opt8) + 대비 9종 (opt9~opt17)
_OPTIONS = [
    # 밝기 (Brightness)
    ("Mean",      "opt1",  "#4ecdc4", True),
    ("Median",    "opt2",  "#ffe66d", True),
    ("RMS",       "opt3",  "#e94560", True),
    ("Top5%",     "opt4",  "#38bdf8", False),
    ("Top1%",     "opt5",  "#fbbf24", False),
    ("P90",       "opt6",  "#a78bfa", False),
    ("BI",        "opt7",  "#f472b6", False),
    ("Log Mean",  "opt8",  "#34d399", False),
    # 대비 (Contrast)
    ("Michelson", "opt9",  "#fb923c", False),
    ("Mich.Loc",  "opt10", "#94a3b8", False),
    ("RMS Cont",  "opt11", "#60a5fa", False),
    ("Weber",     "opt12", "#f87171", False),
    ("SNR",       "opt13", "#4ade80", True),
    ("Dyn.Range", "opt14", "#facc15", False),
    ("Sharpness", "opt15", "#c084fc", False),
    ("Prof.H",    "opt16", "#67e8f9", False),
    ("Prof.V",    "opt17", "#fda4af", False),
]

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
    """Mode 1/2/3 결과 통계 시계열 플롯 (밝기 8종 + 대비 9종, 총 17가지 옵션 지원)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffers = {opt[1]: deque(maxlen=_MAX_POINTS) for opt in _OPTIONS}
        self._curves = {}
        self._checks = {}
        self._build_ui()
        self._install_hover()

    # ── UI ───────────────────────────────────────────────────────
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        # 컨트롤 줄 1
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.chk_enable = QCheckBox("Enable")
        self.chk_enable.setChecked(False)
        row1.addWidget(self.chk_enable)
        row1.addWidget(self._vsep())

        self.radio_snap = QRadioButton("SNAP")
        self.radio_live = QRadioButton("LIVE")
        self.radio_all  = QRadioButton("ALL")
        self.radio_snap.setChecked(True)
        self._grp = QButtonGroup(self)
        self._grp.addButton(self.radio_snap, 0)
        self._grp.addButton(self.radio_live, 1)
        self._grp.addButton(self.radio_all,  2)
        for rb in (self.radio_snap, self.radio_live, self.radio_all):
            row1.addWidget(rb)
        row1.addWidget(self._vsep())

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self.clear)
        row1.addWidget(self.btn_clear)
        
        self.btn_save_csv = QPushButton("Save CSV")
        self.btn_save_csv.clicked.connect(self._save_csv)
        row1.addWidget(self.btn_save_csv)
        row1.addStretch()
        v.addLayout(row1)

        # pyqtgraph
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(axisItems={"bottom": _IntAxis(orientation="bottom")})
        self.plot.setBackground("#0d121d")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Sample #")
        self.plot.setLabel("left", "Value")

        # 라인 및 체크박스 생성 (별도의 위젯으로 분리하여 외부에서 가져다 쓸 수 있게 함)
        self.options_widget = QWidget()
        opt_layout = QVBoxLayout(self.options_widget)
        opt_layout.setContentsMargins(0, 0, 0, 0)
        
        rows = [QHBoxLayout() for _ in range(4)]
        for r in rows:
            r.setSpacing(6)
        # 4 / 4 / 4 / 5 분할
        _ROW_BREAKS = [4, 8, 12]

        for i, (label, key, color, default_on) in enumerate(_OPTIONS):
            c = self.plot.plot(pen=pg.mkPen(color, width=2), name=key)
            c.setVisible(default_on)
            self._curves[key] = c

            chk = self._mk_color_check(label, color, default_on)
            chk.toggled.connect(c.setVisible)
            self._checks[key] = chk

            row_idx = sum(1 for b in _ROW_BREAKS if i >= b)
            rows[row_idx].addWidget(chk)

        for r in rows:
            r.addStretch()
            opt_layout.addLayout(r)

        # ── 표 (그래프와 동일한 데이터, 헤더 클릭으로 정렬 가능) ────────
        self.table = QTableWidget(0, 1 + len(_OPTIONS))
        headers = ["#"] + [opt[0] for opt in _OPTIONS]
        self.table.setHorizontalHeaderLabels(headers)
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

        # 그래프만 메인 레이아웃에 추가 — table 은 별도 dock_proc_table 도킹으로 분리
        v.addWidget(self.plot, 1)

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
            
        # 첫번째 옵션 버퍼의 길이를 기준으로 사용
        ref_key = _OPTIONS[0][1]
        if len(self._buffers[ref_key]) == 0:
            self._vline.hide()
            self._hover_label.setText("")
            return
            
        mouse_pt = vb.mapSceneToView(pos)
        n = len(self._buffers[ref_key])
        idx = int(round(mouse_pt.x()))
        idx = max(0, min(n - 1, idx))

        self._vline.setPos(idx)
        self._vline.show()

        html = [
            f"<div style='background:#0d121d; padding:4px 8px;",
            f" border:1px solid #1e293b; font-family:monospace; font-size:10pt;'>",
            f"<b style='color:#94a3b8;'>Sample {idx}</b><br>"
        ]
        
        for label, key, color, _ in _OPTIONS:
            if self._checks[key].isChecked():
                val = self._buffers[key][idx]
                html.append(f"<span style='color:{color};'>{label} : {val:.4f}</span><br>")
                
        html.append("</div>")
        self._hover_label.setText("".join(html))

    # ── public API ───────────────────────────────────────────────
    def add_point_dict(self, source: str, stats: dict) -> None:
        if not self.chk_enable.isChecked():
            return
        sel = self._grp.checkedId()
        if sel == 0 and source != SOURCE_SNAP:
            return
        if sel == 1 and source != SOURCE_LIVE:
            return
            
        for _, key, _, _ in _OPTIONS:
            val = stats.get(key, 0.0)
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = 0.0
            self._buffers[key].append(val)
            
        self._redraw()
        self._sync_table()

    def clear(self) -> None:
        for buf in self._buffers.values():
            buf.clear()
        self._redraw()
        self._hover_label.setText("")
        self._vline.hide()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setSortingEnabled(True)

    def _save_csv(self) -> None:
        ref_key = _OPTIONS[0][1]
        n = len(self._buffers[ref_key])
        if n == 0:
            QMessageBox.information(self, "No Data", "저장할 데이터가 없습니다.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv)")
        if not path:
            return

        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # 헤더
            headers = ["Sample"] + [opt[0] for opt in _OPTIONS]
            writer.writerow(headers)
            # 데이터
            for r in range(n):
                row_data = [r]
                for _, key, _, _ in _OPTIONS:
                    row_data.append(self._buffers[key][r])
                writer.writerow(row_data)

    # ── internal ────────────────────────────────────────────────
    def _sync_table(self) -> None:
        """ring buffer 를 표와 동기화. 헤더 정렬 보존."""
        was_sorted = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        try:
            ref_key = _OPTIONS[0][1]
            n = len(self._buffers[ref_key])
            self.table.setRowCount(n)
            for r in range(n):
                # # 열
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, int(r))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter) 
                self.table.setItem(r, 0, item)
                
                # 옵션 열들
                for i, (_, key, _, _) in enumerate(_OPTIONS):
                    col_idx = i + 1
                    v = self._buffers[key][r]
                    item = QTableWidgetItem()
                    item.setData(Qt.ItemDataRole.DisplayRole, float(v))
                    item.setText(f"{float(v):.4f}")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter) 
                    self.table.setItem(r, col_idx, item)
        finally:
            self.table.setSortingEnabled(was_sorted)

    def _redraw(self) -> None:
        ref_key = _OPTIONS[0][1]
        n = len(self._buffers[ref_key])
        if n == 0:
            for c in self._curves.values():
                c.setData([], [])
            return
        x = np.arange(n)
        for _, key, _, _ in _OPTIONS:
            data = np.fromiter(self._buffers[key], dtype=float)
            self._curves[key].setData(x, data)

