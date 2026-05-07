"""ui/histogram_range_widget.py
히스토그램 + 듀얼 핸들 범위 슬라이더 위젯.
레이아웃: [Min 패널] [히스토그램+컬러바+핸들] [Max 패널]
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect
from PyQt6.QtGui import (
    QColor, QLinearGradient, QPainter, QPen, QBrush, QFont,
)
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QToolButton,
    QVBoxLayout, QWidget, QSizePolicy,
)


# ── 컬러맵 색상 샘플링 ────────────────────────────────────────────────────────

def _cmap_color(f: float, cmap: str) -> QColor:
    f = max(0.0, min(1.0, float(f)))
    if cmap == 'jet':
        r = max(0.0, min(1.0, 1.5 - abs(4.0 * f - 3.0)))
        g = max(0.0, min(1.0, 1.5 - abs(4.0 * f - 2.0)))
        b = max(0.0, min(1.0, 1.5 - abs(4.0 * f - 1.0)))
    elif cmap in ('grey', 'gray', 'off'):
        r = g = b = f
    elif cmap == 'hot':
        r = max(0.0, min(1.0, f * 3.0))
        g = max(0.0, min(1.0, f * 3.0 - 1.0))
        b = max(0.0, min(1.0, f * 3.0 - 2.0))
    elif cmap == 'viridis':
        r = max(0.0, min(1.0, 0.267 + 0.005*f + 2.33*f**2 - 1.98*f**3))
        g = max(0.0, min(1.0, 0.005 + 1.40*f - 0.55*f**2))
        b = max(0.0, min(1.0, 0.329 + 1.50*f - 1.85*f**2))
    elif cmap == 'plasma':
        r = max(0.0, min(1.0, 0.05 + 2.0*f - 0.7*f**2))
        g = max(0.0, min(1.0, 0.03*f + 1.2*f**2 - 0.5*f**3))
        b = max(0.0, min(1.0, 0.55 - 0.8*f + 0.5*f**2))
    else:
        r = g = b = f
    return QColor(int(r * 255), int(g * 255), int(b * 255))


# ── 핸들 색상 ─────────────────────────────────────────────────────────────────

_CLR_LO = QColor(100, 140, 255)   # 파랑 (min 핸들)
_CLR_HI = QColor(255, 90,  90)    # 빨강 (max 핸들)


# ── 듀얼 핸들 슬라이더 ────────────────────────────────────────────────────────

class _DualHandleSlider(QWidget):
    range_changed = pyqtSignal(float, float)   # (lo_frac, hi_frac) ∈ [0,1]

    _HR        = 10    # 핸들 반지름 (px)
    _BAR_H     = 18    # 컬러바 높이
    _GRAD_STOPS = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lo: float = 0.0
        self._hi: float = 1.0
        self._dragging: str | None = None
        self._pan_start_x: int = 0
        self._pan_start_lo: float = 0.0
        self._pan_start_hi: float = 1.0
        self._hist_counts: np.ndarray | None = None
        self._cmap: str = 'jet'
        self.setMinimumHeight(self._HR * 2 + self._BAR_H + 30)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)

    # ── 공개 API ─────────────────────────────────────────────────────

    def set_fracs(self, lo: float, hi: float):
        self._lo = max(0.0, min(1.0, lo))
        self._hi = max(self._lo + 1e-4, min(1.0, hi))
        self.update()

    def set_histogram(self, counts: np.ndarray):
        self._hist_counts = counts.astype(np.float32)
        self.update()

    def set_colormap(self, cmap: str):
        self._cmap = cmap if cmap != 'off' else 'grey'
        self.update()

    # ── 내부 레이아웃 ─────────────────────────────────────────────────

    def _bar_rect(self) -> tuple[int, int, int, int]:
        r  = self._HR
        w  = self.width()  - 2 * r
        h  = self._BAR_H
        y  = self.height() - r - h
        return r, y, w, h

    def _handle_y(self) -> int:
        _, y, _, h = self._bar_rect()
        return y + h // 2

    def _frac_to_x(self, f: float) -> int:
        x0, _, w, _ = self._bar_rect()
        return x0 + int(f * w)

    def _x_to_frac(self, x: int) -> float:
        x0, _, w, _ = self._bar_rect()
        return max(0.0, min(1.0, (x - x0) / max(w, 1)))

    # ── 페인트 ───────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        x0, by, bw, bh = self._bar_rect()
        hy      = self._handle_y()
        hist_h  = by - 2

        lo_x = self._frac_to_x(self._lo)
        hi_x = self._frac_to_x(self._hi)

        # ① 히스토그램 배경
        p.fillRect(0, 0, W, hist_h, QColor(18, 24, 44))

        # ② 히스토그램 바: 범위 밖은 파랑/빨강, 안은 컬러맵 색
        if self._hist_counts is not None and len(self._hist_counts) > 0:
            counts = self._hist_counts
            max_c  = float(counts.max()) or 1.0
            n      = len(counts)
            bw_bar = bw / n
            p.setPen(Qt.PenStyle.NoPen)
            for i, c in enumerate(counts):
                f   = i / max(n - 1, 1)
                if f < self._lo:
                    col = QColor(60, 80, 200, 200)   # 파랑 (under)
                elif f > self._hi:
                    col = QColor(200, 50, 50, 200)   # 빨강 (over)
                else:
                    # 범위 안: 선택 구간 내에서 0→1 리스케일
                    rng = max(self._hi - self._lo, 1e-6)
                    col = _cmap_color((f - self._lo) / rng, self._cmap)
                    col.setAlpha(200)
                p.setBrush(QBrush(col))
                bh_i = int(c / max_c * (hist_h - 4))
                px   = x0 + int(i * bw_bar)
                pw   = max(1, int(bw_bar) + 1)
                p.drawRect(px, hist_h - bh_i, pw, bh_i)

        # ③ 수직 가이드선
        for lx, col in ((lo_x, _CLR_LO), (hi_x, _CLR_HI)):
            c2 = QColor(col); c2.setAlpha(200)
            p.setPen(QPen(c2, 1, Qt.PenStyle.SolidLine))
            p.drawLine(lx, 0, lx, hist_h)

        # ④ 컬러바: [파랑] [컬러맵 그라디언트] [빨강]
        p.setPen(Qt.PenStyle.NoPen)
        # 왼쪽 under 구간 → 파랑
        if lo_x > x0:
            p.fillRect(x0, by, lo_x - x0, bh, QColor(60, 80, 220))
        # 중간 in-range → 컬러맵 그라디언트 (0→1 풀 스케일)
        if hi_x > lo_x:
            grad = QLinearGradient(lo_x, 0, hi_x, 0)
            for i in range(self._GRAD_STOPS + 1):
                grad.setColorAt(i / self._GRAD_STOPS,
                                _cmap_color(i / self._GRAD_STOPS, self._cmap))
            p.setBrush(QBrush(grad))
            p.drawRect(lo_x, by, hi_x - lo_x, bh)
        # 오른쪽 over 구간 → 빨강
        if hi_x < x0 + bw:
            p.fillRect(hi_x, by, (x0 + bw) - hi_x, bh, QColor(220, 50, 50))

        # ⑤ 핸들 (원형, 테두리 포함)
        for lx, col in ((lo_x, _CLR_LO), (hi_x, _CLR_HI)):
            p.setPen(QPen(QColor(220, 220, 220, 230), 1.5))
            p.setBrush(QBrush(col))
            p.drawEllipse(QPoint(lx, hy), self._HR, self._HR)

        p.end()

    # ── 마우스 ────────────────────────────────────────────────────────

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        x    = ev.pos().x()
        lo_x = self._frac_to_x(self._lo)
        hi_x = self._frac_to_x(self._hi)
        r    = self._HR + 5
        d_lo = abs(x - lo_x)
        d_hi = abs(x - hi_x)
        if d_lo <= r and d_lo <= d_hi:
            self._dragging = 'lo'
        elif d_hi <= r:
            self._dragging = 'hi'
        elif lo_x < x < hi_x:
            self._dragging = 'pan'
            self._pan_start_x  = x
            self._pan_start_lo = self._lo
            self._pan_start_hi = self._hi
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        x = ev.pos().x()
        if self._dragging is None:
            # 커서 힌트
            lo_x = self._frac_to_x(self._lo)
            hi_x = self._frac_to_x(self._hi)
            r = self._HR + 5
            if abs(x - lo_x) <= r or abs(x - hi_x) <= r:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif lo_x < x < hi_x:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if self._dragging == 'pan':
            _, _, w, _ = self._bar_rect()
            dx = (x - self._pan_start_x) / max(w, 1)
            gap = self._pan_start_hi - self._pan_start_lo
            new_lo = max(0.0, min(1.0 - gap, self._pan_start_lo + dx))
            self._lo = new_lo
            self._hi = new_lo + gap
        else:
            f = self._x_to_frac(x)
            if self._dragging == 'lo':
                self._lo = min(f, self._hi - 1e-4)
            else:
                self._hi = max(f, self._lo + 1e-4)

        self.range_changed.emit(self._lo, self._hi)
        self.update()

    def mouseReleaseEvent(self, _):
        self._dragging = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def hideEvent(self, ev):
        if self._dragging is not None:
            self._dragging = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hideEvent(ev)


# ── 좌우 값 패널 ──────────────────────────────────────────────────────────────

def _make_side_panel(title: str, handle_color: QColor) -> tuple[QWidget, QLabel]:
    """레이블 + 값 박스로 구성된 좌/우 패널."""
    r, g, b = handle_color.red(), handle_color.green(), handle_color.blue()
    panel = QWidget()
    panel.setFixedWidth(82)
    panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    panel.setStyleSheet(f"background: #0c1428; border: 1px solid #1a2840; border-radius: 4px;")

    v = QVBoxLayout(panel)
    v.setContentsMargins(4, 6, 4, 6)
    v.setSpacing(4)

    lbl_title = QLabel(title)
    lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl_title.setStyleSheet(
        f"color: rgb({r},{g},{b}); font-family:'Segoe UI'; font-size:11px;"
        "border:none; background:transparent;"
    )
    lbl_title.setWordWrap(True)

    lbl_val = QLabel("0")
    lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl_val.setStyleSheet(
        "color: #d0deff; font-family:'Courier New'; font-size:13px; font-weight:bold;"
        "background: #080e1e; border: 1px solid #1a2840; border-radius: 3px;"
        "padding: 4px 2px; min-height:28px; border:none;"
    )

    v.addWidget(lbl_title)
    v.addWidget(lbl_val)
    v.addStretch()
    return panel, lbl_val


# ── 메인 위젯 ─────────────────────────────────────────────────────────────────

_BTN = (
    "QPushButton { background:#0d2038; color:#a0c0e0; border:1px solid #1a3a60;"
    "border-radius:3px; font-size:11px; padding:3px 10px; }"
    "QPushButton:hover { background:#1a3a60; color:#fff; }"
)
_ICON_BTN = (
    "QToolButton { background:#0d2038; color:#a0c0e0; border:1px solid #1a3a60;"
    "border-radius:3px; font-size:14px; padding:2px 6px; }"
    "QToolButton:hover { background:#1a3a60; color:#fff; }"
)


class HistogramRangeWidget(QWidget):
    """이미지 히스토그램 + 듀얼 핸들 범위 슬라이더.

    사용법:
        widget.update_image(ndarray, cmap='jet')
        widget.range_changed.connect(callback)   # (vmin, vmax)
    """

    range_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("histRangeWidget")
        self.setStyleSheet("#histRangeWidget { background:#0a1020; }")
        self._data_min   = 0.0   # 이미지 전체 raw min
        self._data_max   = 1.0   # 이미지 전체 raw max
        self._slider_min = 0.0   # 슬라이더가 표현하는 구간 min (줌인 가능)
        self._slider_max = 1.0   # 슬라이더가 표현하는 구간 max
        self._vmin       = 0.0
        self._vmax       = 1.0
        self._image: np.ndarray | None = None
        self._cmap = 'jet'

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 4)
        outer.setSpacing(4)

        # ── 가운데 행: [Min 패널] [슬라이더] [Max 패널] ──
        center = QHBoxLayout()
        center.setSpacing(6)

        self._panel_lo, self._lbl_min = _make_side_panel("Min\n(counts)", _CLR_LO)
        self._panel_hi, self._lbl_max = _make_side_panel("Max\n(counts)", _CLR_HI)

        self._slider = _DualHandleSlider()
        self._slider.range_changed.connect(self._on_frac_changed)

        center.addWidget(self._panel_lo)
        center.addWidget(self._slider, 1)
        center.addWidget(self._panel_hi)
        outer.addLayout(center, 1)

        # ── 버튼 행 ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        btn_opt  = QPushButton("Optimal Scale")
        btn_full = QPushButton("Full Scale")
        btn_opt.setStyleSheet(_BTN)
        btn_full.setStyleSheet(_BTN)
        btn_opt.clicked.connect(self._optimal_scale)
        btn_full.clicked.connect(self._full_scale)

        btn_zoom = QToolButton()
        btn_zoom.setText("🔍")
        btn_zoom.setToolTip("Optimal Scale (99.5% 퍼센타일)")
        btn_zoom.setStyleSheet(_ICON_BTN)
        btn_zoom.clicked.connect(self._optimal_scale)

        btn_row.addStretch()
        btn_row.addWidget(btn_opt)
        btn_row.addWidget(btn_full)
        btn_row.addWidget(btn_zoom)
        outer.addLayout(btn_row)

    # ── 공개 API ─────────────────────────────────────────────────────

    def update_image(self, image: np.ndarray, cmap: str = 'jet',
                     reset_range: bool = False):
        if image is None or image.size == 0:
            return
        first_load = (self._image is None)
        self._image = image
        self._cmap  = cmap
        flat = image.ravel().astype(np.float64)
        self._data_min = float(flat.min())
        self._data_max = float(flat.max())

        if first_load or reset_range:
            self._vmin       = self._data_min
            self._vmax       = self._data_max
            self._slider_min = self._data_min
            self._slider_max = self._data_max

        self._slider.set_colormap(cmap)
        self._recompute_histogram()
        self._sync_fracs()
        self._update_labels()

    def set_vrange(self, vmin: float, vmax: float):
        self._vmin = float(vmin)
        self._vmax = float(vmax)
        self._sync_fracs()
        self._update_labels()

    def get_vrange(self) -> tuple[float, float]:
        return self._vmin, self._vmax

    def set_range(self, vmin: float, vmax: float):
        """외부에서 vmin/vmax 직접 지정 — 슬라이더 핸들 동기화.
        ROI Range 기능 등 프로그래밍 방식의 범위 설정에 사용."""
        if vmax <= vmin:
            vmax = vmin + 1.0
        # 슬라이더 구간이 지정 범위를 포함하도록 확장
        if vmin < self._slider_min or vmax > self._slider_max:
            self._slider_min = min(self._slider_min, vmin)
            self._slider_max = max(self._slider_max, vmax)
            self._recompute_histogram()
        self._vmin = vmin
        self._vmax = vmax
        self._sync_fracs()
        self._update_labels()
        # range_changed 는 emit 하지 않음 — 외부에서 이미 처리 중

    # ── 내부 ─────────────────────────────────────────────────────────

    def _sync_fracs(self):
        rng = self._slider_max - self._slider_min or 1.0
        lo  = (self._vmin - self._slider_min) / rng
        hi  = (self._vmax - self._slider_min) / rng
        self._slider.set_fracs(
            max(0.0, min(1.0, lo)),
            max(0.0, min(1.0, hi)),
        )

    def _on_frac_changed(self, lo: float, hi: float):
        rng = self._slider_max - self._slider_min
        self._vmin = self._slider_min + lo * rng
        self._vmax = self._slider_min + hi * rng
        self._update_labels()
        self.range_changed.emit(self._vmin, self._vmax)

    def _recompute_histogram(self):
        if self._image is None:
            return
        flat = self._image.ravel().astype(np.float64)
        lo = self._slider_min
        hi = self._slider_max
        if hi <= lo:
            hi = lo + 1.0
        counts, _ = np.histogram(flat, bins=256, range=(lo, hi))
        self._slider.set_histogram(counts)

    def _update_labels(self):
        self._lbl_min.setText(f"{self._vmin:.0f}")
        self._lbl_max.setText(f"{self._vmax:.0f}")

    def _optimal_scale(self):
        if self._image is None:
            return
        flat = self._image.ravel().astype(np.float64)
        opt_lo = float(np.percentile(flat, 0.5))
        opt_hi = float(np.percentile(flat, 99.5))
        # 슬라이더 구간을 optimal 범위로 줌인 → 더 세밀한 조작 가능
        self._slider_min = opt_lo
        self._slider_max = opt_hi
        self._vmin = opt_lo
        self._vmax = opt_hi
        self._recompute_histogram()
        self._slider.set_fracs(0.0, 1.0)
        self._update_labels()
        self.range_changed.emit(self._vmin, self._vmax)

    def _full_scale(self):
        # 슬라이더 구간을 전체 데이터 범위로 복원
        self._slider_min = self._data_min
        self._slider_max = self._data_max
        self._vmin = self._data_min
        self._vmax = self._data_max
        self._recompute_histogram()
        self._slider.set_fracs(0.0, 1.0)
        self._update_labels()
        self.range_changed.emit(self._vmin, self._vmax)
