"""ui/histogram_range_widget.py
히스토그램 + 듀얼 핸들 범위 슬라이더 위젯.
컬러맵 min/max 를 인터랙티브하게 조절한다.
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import (
    QColor, QLinearGradient, QPainter, QPen, QBrush, QFont,
)
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


# ── 컬러맵 색상 샘플링 (colormap_utils 재사용) ────────────────────────────────

def _cmap_color(f: float, cmap: str) -> QColor:
    """f ∈ [0,1] → 컬러맵 QColor."""
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


# ── 듀얼 핸들 슬라이더 (히스토그램 + 그라디언트 바 + 핸들) ─────────────────────

class _DualHandleSlider(QWidget):
    """히스토그램 오버레이 + 컬러맵 그라디언트 바 + 드래그 핸들 두 개."""

    range_changed = pyqtSignal(float, float)  # (lo_frac, hi_frac) ∈ [0,1]

    _HR = 9          # 핸들 반지름 (px)
    _BAR_H = 14      # 그라디언트 바 높이 (px)
    _GRAD_STOPS = 32 # 그라디언트 색상 정지점 수

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lo: float = 0.0
        self._hi: float = 1.0
        self._dragging: str | None = None   # 'lo' | 'hi'
        self._hist_counts: np.ndarray | None = None
        self._cmap: str = 'jet'
        self.setMinimumHeight(self._HR * 2 + self._BAR_H + 44)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.ArrowCursor)

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

    # ── 내부 레이아웃 계산 ────────────────────────────────────────────

    def _bar_rect(self) -> tuple[int, int, int, int]:
        """그라디언트 바 (x, y, w, h)."""
        r = self._HR
        w = self.width() - 2 * r
        h = self._BAR_H
        y = self.height() - r - h
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

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        x0, by, bw, bh = self._bar_rect()
        hy = self._handle_y()
        hist_h = by - 4           # 히스토그램 영역 높이

        # ① 히스토그램 배경
        p.fillRect(0, 0, W, hist_h, QColor(20, 28, 50))

        # ② 히스토그램 바
        if self._hist_counts is not None and len(self._hist_counts) > 0:
            counts = self._hist_counts
            max_c = float(counts.max()) or 1.0
            n = len(counts)
            bar_w = bw / n
            p.setPen(Qt.PenStyle.NoPen)
            for i, c in enumerate(counts):
                f = i / max(n - 1, 1)
                col = _cmap_color(f, self._cmap)
                col.setAlpha(180)
                p.setBrush(QBrush(col))
                bh_i = int(c / max_c * (hist_h - 2))
                px = x0 + int(i * bar_w)
                pw = max(1, int(bar_w) + 1)
                p.drawRect(px, hist_h - bh_i, pw, bh_i)

        # ③ 핸들 위치 수직선 (히스토그램 위)
        lo_x = self._frac_to_x(self._lo)
        hi_x = self._frac_to_x(self._hi)
        for lx, color in ((lo_x, QColor(120, 180, 255, 220)),
                          (hi_x, QColor(255, 120, 120, 220))):
            p.setPen(QPen(color, 1, Qt.PenStyle.SolidLine))
            p.drawLine(lx, 0, lx, hist_h)

        # ④ 그라디언트 바
        grad = QLinearGradient(x0, 0, x0 + bw, 0)
        for i in range(self._GRAD_STOPS + 1):
            f = i / self._GRAD_STOPS
            grad.setColorAt(f, _cmap_color(f, self._cmap))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRect(x0, by, bw, bh)

        # 선택 영역 외부를 반투명 검정으로 어둡게
        dim = QColor(0, 0, 0, 140)
        if lo_x > x0:
            p.fillRect(x0, by, lo_x - x0, bh, dim)
        if hi_x < x0 + bw:
            p.fillRect(hi_x, by, (x0 + bw) - hi_x, bh, dim)

        # ⑤ 핸들 (원형)
        for x, col in ((lo_x, QColor(120, 180, 255)),
                       (hi_x, QColor(255, 120, 120))):
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
            p.setBrush(QBrush(col))
            p.drawEllipse(QPoint(x, hy), self._HR, self._HR)

        p.end()

    # ── 마우스 이벤트 ─────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.pos().x()
        hy = self._handle_y()
        lo_x = self._frac_to_x(self._lo)
        hi_x = self._frac_to_x(self._hi)
        r = self._HR + 4
        d_lo = abs(x - lo_x)
        d_hi = abs(x - hi_x)
        if d_lo <= r and d_lo <= d_hi:
            self._dragging = 'lo'
        elif d_hi <= r:
            self._dragging = 'hi'

    def mouseMoveEvent(self, event):
        if self._dragging is None:
            return
        f = self._x_to_frac(event.pos().x())
        if self._dragging == 'lo':
            self._lo = min(f, self._hi - 1e-4)
        else:
            self._hi = max(f, self._lo + 1e-4)
        self.range_changed.emit(self._lo, self._hi)
        self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = None


# ── 히스토그램 범위 위젯 (슬라이더 + 레이블 + 버튼) ──────────────────────────

_BTN = (
    "QPushButton { background:#0d2038; color:#a0c0e0; border:1px solid #1a3a60;"
    "border-radius:3px; font-size:11px; padding:2px 8px; }"
    "QPushButton:hover { background:#1a3a60; color:#fff; }"
)
_LBL = "color:#8090b0; font-family:'Courier New'; font-size:11px;"


class HistogramRangeWidget(QWidget):
    """이미지 히스토그램 + 듀얼 핸들 범위 슬라이더.

    사용법:
        widget.update_image(ndarray, cmap='jet')   # 이미지 갱신
        widget.range_changed.connect(callback)      # (vmin, vmax) 콜백
    """

    range_changed = pyqtSignal(float, float)   # (vmin, vmax) 데이터 좌표

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_min = 0.0
        self._data_max = 1.0
        self._vmin = 0.0
        self._vmax = 1.0
        self._image: np.ndarray | None = None
        self._cmap = 'jet'

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)

        self._slider = _DualHandleSlider()
        self._slider.range_changed.connect(self._on_frac_changed)
        layout.addWidget(self._slider)

        # 하단 행: min값 / 버튼 / max값
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 0, 0, 0)
        bot.setSpacing(6)

        self._lbl_min = QLabel("0")
        self._lbl_min.setStyleSheet(_LBL)
        self._lbl_min.setFixedWidth(70)

        self._lbl_max = QLabel("65535")
        self._lbl_max.setStyleSheet(_LBL)
        self._lbl_max.setFixedWidth(70)
        self._lbl_max.setAlignment(Qt.AlignmentFlag.AlignRight)

        btn_opt  = QPushButton("Optimal Scale")
        btn_full = QPushButton("Full Scale")
        btn_opt.setStyleSheet(_BTN)
        btn_full.setStyleSheet(_BTN)
        btn_opt.clicked.connect(self._optimal_scale)
        btn_full.clicked.connect(self._full_scale)

        bot.addWidget(self._lbl_min)
        bot.addStretch()
        bot.addWidget(btn_opt)
        bot.addWidget(btn_full)
        bot.addStretch()
        bot.addWidget(self._lbl_max)
        layout.addLayout(bot)

    # ── 공개 API ─────────────────────────────────────────────────────

    def update_image(self, image: np.ndarray, cmap: str = 'jet'):
        """이미지 갱신: 히스토그램 재계산 + 슬라이더 범위 반영."""
        if image is None or image.size == 0:
            return
        self._image = image
        self._cmap = cmap
        flat = image.ravel().astype(np.float64)
        self._data_min = float(flat.min())
        self._data_max = float(flat.max())

        counts, _ = np.histogram(flat, bins=256,
                                  range=(self._data_min, self._data_max))
        self._slider.set_histogram(counts)
        self._slider.set_colormap(cmap)
        self._sync_fracs()

    def set_vrange(self, vmin: float, vmax: float):
        """외부에서 범위를 직접 설정한다."""
        self._vmin = float(vmin)
        self._vmax = float(vmax)
        self._sync_fracs()
        self._update_labels()

    def get_vrange(self) -> tuple[float, float]:
        return self._vmin, self._vmax

    # ── 내부 ─────────────────────────────────────────────────────────

    def _sync_fracs(self):
        rng = self._data_max - self._data_min or 1.0
        lo = (self._vmin - self._data_min) / rng
        hi = (self._vmax - self._data_min) / rng
        self._slider.set_fracs(
            max(0.0, min(1.0, lo)),
            max(0.0, min(1.0, hi)),
        )

    def _on_frac_changed(self, lo: float, hi: float):
        rng = self._data_max - self._data_min
        self._vmin = self._data_min + lo * rng
        self._vmax = self._data_min + hi * rng
        self._update_labels()
        self.range_changed.emit(self._vmin, self._vmax)

    def _update_labels(self):
        self._lbl_min.setText(f"{self._vmin:.1f}")
        self._lbl_max.setText(f"{self._vmax:.1f}")

    def _optimal_scale(self):
        if self._image is None:
            return
        flat = self._image.ravel().astype(np.float64)
        self._vmin = float(np.percentile(flat, 0.5))
        self._vmax = float(np.percentile(flat, 99.5))
        self._sync_fracs()
        self._update_labels()
        self.range_changed.emit(self._vmin, self._vmax)

    def _full_scale(self):
        self._vmin = self._data_min
        self._vmax = self._data_max
        self._slider.set_fracs(0.0, 1.0)
        self._update_labels()
        self.range_changed.emit(self._vmin, self._vmax)
