"""
plot_panel.py
다중 프로파일 플롯 패널
체크박스로 선택된 프레임들의 프로파일을 겹쳐서 표시
"""

import csv
import os
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QToolButton, QFileDialog,
)


class PlotPanel(QWidget):
    def __init__(self, title: str = "Profile", enable_xmin_zero: bool = True, parent=None):
        super().__init__(parent)
        self._title = title
        self._enable_xmin_zero = enable_xmin_zero
        self._plot_items = []
        self._colors = [
            '#e94560', '#4ecdc4', '#ffe66d', '#a8e6cf',
            '#ff8b94', '#c7ceea', '#ffd3b6', '#d4f1f4',
            '#f6e58d', '#ff9ff3'
        ]
        self._color_idx = 0
        self._frozen = False   # #22 플롯 Freeze
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

        # #22 플롯 Freeze 버튼
        self.btn_freeze = QToolButton()
        self.btn_freeze.setText("❄")
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.setToolTip("플롯 갱신 정지 (현재 그래프 유지)")
        _tool_style = """
            QToolButton { background: transparent; color: #4a6a8a;
                border: 1px solid #1a3050; border-radius: 3px;
                font-size: 12px; padding: 1px 5px; }
            QToolButton:checked { background: #0d2040; color: #a0c8ff;
                border-color: #4080c0; }
            QToolButton:hover { border-color: #4ecdc4; }
        """
        self.btn_freeze.setStyleSheet(_tool_style)
        self.btn_freeze.toggled.connect(self._on_freeze_toggled)
        header_row.addWidget(self.btn_freeze)

        # CSV 내보내기 버튼 (P3-1)
        self.btn_csv = QToolButton()
        self.btn_csv.setText("💾")
        self.btn_csv.setToolTip("현재 프로파일 CSV 저장")
        self.btn_csv.setStyleSheet(_tool_style)
        self.btn_csv.clicked.connect(self._save_csv)
        header_row.addWidget(self.btn_csv)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setFixedWidth(60)
        self.btn_clear.clicked.connect(self.clear)
        header_row.addWidget(self.btn_clear)
        layout.addLayout(header_row)

        # 피크 정보 레이블 (P1-2)
        self.peak_label = QLabel("")
        self.peak_label.setStyleSheet(
            "color: #ffe66d; font-size: 10px; padding: 1px 4px;"
        )
        self.peak_label.setWordWrap(True)
        layout.addWidget(self.peak_label)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#16213e')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend(offset=(10, 10))
        for axis in ('bottom', 'left'):
            ax = self.plot_widget.getAxis(axis)
            ax.setPen('#0f3460')
            ax.setTextPen('#a0a0b0')
        self.plot_widget.setLabel('bottom', 'X',
                                  **{'color': '#607090', 'font-size': '10pt'})
        self.plot_widget.setLabel('left',   'Y',
                                  **{'color': '#607090', 'font-size': '10pt'})
        # 확대/축소/패닝 시에도 X축이 0 아래로 내려가지 않도록 ViewBox에 하한선 고정
        if self._enable_xmin_zero:
            self.plot_widget.getPlotItem().getViewBox().setLimits(xMin=0)
        layout.addWidget(self.plot_widget)

        # ── 마우스 호버 crosshair (수직선만) ──
        self._vline = pg.InfiniteLine(angle=90, movable=False,
                                      pen=pg.mkPen('#ffe66d', width=1, style=Qt.PenStyle.DashLine))
        self._hline = pg.InfiniteLine(angle=0, movable=False,
                                      pen=pg.mkPen('#ffe66d', width=1, style=Qt.PenStyle.DashLine))
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self.plot_widget.addItem(self._vline, ignoreBounds=True)
        self.plot_widget.addItem(self._hline, ignoreBounds=True)

        # ── MATLAB-style datatip ──
        self._dot = pg.ScatterPlotItem(
            size=9,
            pen=pg.mkPen('#ffe66d', width=1.5),
            brush=pg.mkBrush(255, 230, 109, 160),
        )
        self._dot.setVisible(False)
        self.plot_widget.addItem(self._dot)

        self._tip = pg.TextItem(text="", anchor=(0, 1))
        self._tip.setVisible(False)
        self._tip.fill   = pg.mkBrush(10, 20, 45, 220)
        self._tip.border = pg.mkPen('#ffe66d', width=1)
        self.plot_widget.addItem(self._tip)

        # ── 헤더 상태 레이블 ──
        self.hover_label = QLabel("—")
        self.hover_label.setStyleSheet(
            "color: #ffe66d; font-size: 10px; padding: 0 4px;"
        )
        header_row.addWidget(self.hover_label)

        self._proxy = pg.SignalProxy(
            self.plot_widget.scene().sigMouseMoved,
            rateLimit=30, slot=self._on_mouse_moved
        )

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def plot_line(self, data: np.ndarray, label: str = "", clear_first: bool = True):
        if self._frozen: return   # #22
        if clear_first:
            self.clear()
        self._add_line(data, label)

    def plot_line_overlay(self, data: np.ndarray, label: str = ""):
        """기존 플롯 위에 겹쳐서 추가 (체크박스 다중 선택용)"""
        if self._frozen: return   # #22
        self._add_line(data, label)

    def plot_two_lines(self, data1: np.ndarray, data2: np.ndarray,
                       label1: str = "X mean", label2: str = "Y mean",
                       clear_first: bool = True):
        if self._frozen: return   # #22
        if clear_first:
            self.clear()
        self._add_line(data1, label1)
        self._add_line(data2, label2)

    def plot_multi_frames(self, profiles: list, labels: list):
        """여러 프레임 프로파일 한번에 그리기"""
        if self._frozen: return   # #22
        self.clear()
        for data, label in zip(profiles, labels):
            self._add_line(data, label)

    def _on_mouse_moved(self, evt):
        pos = evt[0]
        vb = self.plot_widget.getPlotItem().getViewBox()
        if not self.plot_widget.sceneBoundingRect().contains(pos):
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            self._dot.setVisible(False)
            self._tip.setVisible(False)
            self.hover_label.setText("—")
            return

        mp = vb.mapSceneToView(pos)
        x = mp.x()

        # 모든 플롯에서 가장 가까운 데이터 포인트 찾기
        best = None   # (dist, xi, yi, label, color)
        for item in self._plot_items:
            xdata, ydata = item.getData()
            if xdata is None or ydata is None or len(xdata) == 0:
                continue
            idx = int(round(x))
            idx = max(0, min(idx, len(ydata) - 1))
            xi, yi = float(xdata[idx]), float(ydata[idx])
            dist = abs(x - xi)
            color = item.opts.get('pen') or '#ffe66d'
            if best is None or dist < best[0]:
                best = (dist, xi, yi, item.name() or "", color)

        if best is None:
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            self._dot.setVisible(False)
            self._tip.setVisible(False)
            self.hover_label.setText("—")
            return

        _, xi, yi, label, color = best

        # 수직 crosshair
        self._vline.setPos(xi)
        self._hline.setPos(yi)
        self._vline.setVisible(True)
        self._hline.setVisible(True)

        # dot marker
        self._dot.setData([xi], [yi])
        self._dot.setVisible(True)

        # MATLAB-style datatip 텍스트
        lbl_line = f"[{label}]\n" if label else ""
        tip_text = f"{lbl_line}X: {int(xi)}\nY: {yi:.2f}"
        self._tip.setText(tip_text)

        # 뷰박스 우측 끝에 가까우면 왼쪽에 표시
        vr = vb.viewRange()
        x_frac = (xi - vr[0][0]) / max(vr[0][1] - vr[0][0], 1e-9)
        anchor = (0, 1) if x_frac < 0.75 else (1, 1)
        self._tip.setAnchor(anchor)
        self._tip.setPos(xi, yi)
        self._tip.setVisible(True)

        # 헤더 레이블
        lbl_str = f" [{label}]" if label else ""
        self.hover_label.setText(f"X:{int(xi)}  Y:{yi:.2f}{lbl_str}")

    def _on_freeze_toggled(self, checked: bool):
        """#22 플롯 Freeze 상태 변경."""
        self._frozen = checked
        self.title_label.setStyleSheet(
            "color: #a0c8ff; font-weight: bold; font-size: 11px; letter-spacing: 1px;"
            if checked else
            "color: #e94560; font-weight: bold; font-size: 11px; letter-spacing: 1px;"
        )

    def _save_csv(self):
        """현재 플롯된 프로파일 데이터를 CSV로 저장 (P3-1)."""
        if not self._plot_items:
            return
        default_name = (
            f"profile_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "프로파일 CSV 저장", default_name,
            "CSV 파일 (*.csv);;모든 파일 (*)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if not path:
            return
        try:
            cols = []
            headers = []
            for i, item in enumerate(self._plot_items):
                xd, yd = item.getData()
                if yd is None:
                    continue
                if not cols:
                    cols.append(xd)
                    headers.append("index")
                cols.append(yd)
                name = item.name() or f"profile_{i+1}"
                headers.append(name)

            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                n_rows = max(len(c) for c in cols)
                for r in range(n_rows):
                    row = []
                    for c in cols:
                        row.append(c[r] if r < len(c) else "")
                    writer.writerow(row)
        except Exception as e:
            print(f"[PlotPanel] CSV 저장 오류: {e}")

    def plot_peaks(self, profile: np.ndarray, peaks: list, label: str = ""):
        """
        피크 위치를 현재 플롯 위에 오버레이 (P1-2).

        Args:
            profile: 1D 원본 프로파일
            peaks:   PeakResult 목록 (core.peak_finder)
            label:   플롯 범례 레이블
        """
        if not peaks:
            self.peak_label.setText("")
            return
        xs = [p.best_center for p in peaks]
        ys = [float(profile[min(int(round(p.position)), len(profile)-1)])
              for p in peaks]
        scatter = self.plot_widget.plot(
            xs, ys,
            pen=None,
            symbol='t',
            symbolBrush=pg.mkBrush('#ffe66d'),
            symbolSize=10,
            name=label or "peaks",
        )
        self._plot_items.append(scatter)

        # 피크 정보 텍스트
        lines = []
        for i, p in enumerate(peaks[:6]):   # 최대 6개 표시
            if p.fit_type != "none" and p.fit_fwhm is not None:
                lines.append(
                    f"P{i+1}: pos={p.best_center:.1f}  "
                    f"FWHM={p.fit_fwhm:.1f}  R²={p.fit_r2:.3f}"
                )
            else:
                lines.append(
                    f"P{i+1}: pos={p.position:.1f}  "
                    f"FWHM={p.fwhm:.1f}  h={p.height:.1f}"
                )
        self.peak_label.setText("\n".join(lines))

    def clear(self):
        self.plot_widget.clear()
        self._plot_items.clear()
        self._color_idx = 0
        self.peak_label.setText("")
        self.hover_label.setText("—")
        # plot_widget.clear()가 모든 아이템 제거 → 재등록
        for item in (self._vline, self._hline):
            self.plot_widget.addItem(item, ignoreBounds=True)
            item.setVisible(False)
        self.plot_widget.addItem(self._dot)
        self._dot.setVisible(False)
        self.plot_widget.addItem(self._tip)
        self._tip.setVisible(False)
        # 범례 재생성
        self.plot_widget.addLegend(offset=(10, 10))

    def set_xlabel(self, label: str):
        self.plot_widget.setLabel('bottom', label,
                                  **{'color': '#607090', 'font-size': '10pt'})

    def set_ylabel(self, label: str):
        self.plot_widget.setLabel('left', label,
                                  **{'color': '#607090', 'font-size': '10pt'})

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
        # Y축 범위: 데이터 최소~최대 (5% 여유)
        self._update_y_range()

    def _update_y_range(self):
        """모든 플롯 기준 Y축 범위 자동 설정 (Y 최솟값은 0 이하로 내려가지 않음)"""
        all_y = []
        for item in self._plot_items:
            y = item.getData()[1]
            if y is not None and len(y) > 0:
                all_y.extend(y.tolist())
        if not all_y:
            return
        ymin = min(all_y)
        ymax = max(all_y)
        margin = (ymax - ymin) * 0.05 if ymax > ymin else 1.0
        self.plot_widget.setYRange(ymin - margin, ymax + margin, padding=0)


class HistogramPanel(QWidget):
    """히스토그램 전용 패널 - 현재 프레임 선택 영역만 표시"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frozen = False   # #22
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        self.title_lbl = QLabel("HISTOGRAM")
        self.title_lbl.setStyleSheet(
            "color: #4ecdc4; font-weight: bold; font-size: 11px; letter-spacing: 1px;"
        )
        header_row.addWidget(self.title_lbl)
        header_row.addStretch()

        # #22 Freeze 버튼
        self.btn_freeze = QToolButton()
        self.btn_freeze.setText("❄")
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.setToolTip("히스토그램 갱신 정지")
        self.btn_freeze.setStyleSheet("""
            QToolButton { background: transparent; color: #4a6a8a;
                border: 1px solid #1a3050; border-radius: 3px;
                font-size: 12px; padding: 1px 5px; }
            QToolButton:checked { background: #0d2040; color: #a0c8ff;
                border-color: #4080c0; }
            QToolButton:hover { border-color: #4ecdc4; }
        """)
        self.btn_freeze.toggled.connect(self._on_freeze_toggled)
        header_row.addWidget(self.btn_freeze)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setFixedWidth(60)
        self.btn_clear.clicked.connect(self.clear)
        header_row.addWidget(self.btn_clear)
        layout.addLayout(header_row)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#16213e')
        self.plot_widget.showGrid(x=True, y=False, alpha=0.3)
        self.plot_widget.setLabel('bottom', 'Pixel Value')
        self.plot_widget.setLabel('left', 'Count')
        for axis in ('bottom', 'left'):
            self.plot_widget.getAxis(axis).setPen('#0f3460')
            self.plot_widget.getAxis(axis).setTextPen('#a0a0b0')
        layout.addWidget(self.plot_widget)

        self._bar_item = None

        # 통계 표시 라벨
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #a0a0b0; font-size: 10px; padding: 2px 4px;")
        layout.addWidget(self.stats_label)

    def _on_freeze_toggled(self, checked: bool):
        self._frozen = checked
        self.title_lbl.setStyleSheet(
            "color: #a0c8ff; font-weight: bold; font-size: 11px; letter-spacing: 1px;"
            if checked else
            "color: #4ecdc4; font-weight: bold; font-size: 11px; letter-spacing: 1px;"
        )

    def plot_histogram(self, counts: np.ndarray, bin_edges: np.ndarray):
        if self._frozen: return   # #22
        self.clear()
        # BarGraphItem 으로 히스토그램 표시
        x = bin_edges[:-1]
        width = bin_edges[1] - bin_edges[0]
        self._bar_item = pg.BarGraphItem(
            x=x, height=counts, width=width * 0.9,
            brush=pg.mkBrush('#4ecdc4'),
            pen=pg.mkPen('#16213e', width=0.5)
        )
        self.plot_widget.addItem(self._bar_item)

        # 통계
        total = counts.sum()
        peak_val = bin_edges[counts.argmax()]
        mean_val = np.average((bin_edges[:-1] + bin_edges[1:]) / 2, weights=counts)
        self.stats_label.setText(
            f"Pixels: {total}  |  Peak: {peak_val:.1f}  |  Mean: {mean_val:.1f}"
            f"  |  Min: {bin_edges[0]:.1f}  |  Max: {bin_edges[-1]:.1f}"
        )

    def clear(self):
        self.plot_widget.clear()
        self._bar_item = None
        self.stats_label.setText("")