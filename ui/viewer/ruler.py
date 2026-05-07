"""ui/viewer/ruler.py — RulerWidget: pixel ruler with mini profile overlay."""
from __future__ import annotations

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QPen, QColor, QBrush, QPainter, QFont,
)


# ─────────────────────────────────────────────────────────────────────────────
# 눈금자 위젯
# ─────────────────────────────────────────────────────────────────────────────

class RulerWidget(QWidget):
    """픽셀 단위 눈금자 + 미니 프로파일 오버레이.

    수평 룰러: 상단 _prof_size px = 프로파일, 하단 _TICK px = 눈금
    수직 룰러: 좌측 _prof_size px = 프로파일, 우측 _TICK px = 눈금
    구분선 드래그로 프로파일 영역 크기 조절 가능.
    """

    _TICK    = 24   # 눈금 영역 고정 크기 (px)
    _SEP_HIT = 5    # 구분선 감지 반경 (px)
    _PROF_MIN = 20
    _PROF_MAX = 200

    def __init__(self, orientation: str = 'horizontal', parent=None):
        super().__init__(parent)
        self._orientation = orientation
        self._scale    = 1.0
        self._offset   = 0.0
        self._img_size = 1000
        self._profile: np.ndarray | None = None
        self._profile_start: int = 0   # 이미지 좌표에서 프로파일 시작 픽셀
        self._prof_size: int = 52   # 드래그로 조절 가능
        self._resizing = False

        self.setMouseTracking(True)
        self._update_fixed_size()
        self.setStyleSheet("background-color: #16213e;")

    def _update_fixed_size(self):
        total = self._prof_size + self._TICK
        if self._orientation == 'horizontal':
            self.setFixedHeight(total)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(total)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def _sep_pos(self) -> int:
        return self._prof_size

    def _on_sep(self, x: int, y: int) -> bool:
        sep = self._sep_pos()
        if self._orientation == 'horizontal':
            return abs(y - sep) <= self._SEP_HIT
        else:
            return abs(x - sep) <= self._SEP_HIT

    # ── 공개 API ────────────────────────────────────────────────────────

    def update_transform(self, scale: float, offset: float, img_size: int):
        self._scale    = scale
        self._offset   = offset
        self._img_size = img_size
        self.update()

    def set_profile(self, data: np.ndarray | None, data_start: int = 0):
        self._profile = data
        self._profile_start = data_start
        self.update()

    # ── 마우스 (구분선 드래그) ──────────────────────────────────────────

    def mousePressEvent(self, event):
        from PyQt6.QtCore import Qt
        if event.button() == Qt.MouseButton.LeftButton and self._on_sep(event.pos().x(), event.pos().y()):
            self._resizing = True
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        from PyQt6.QtCore import Qt
        x, y = event.pos().x(), event.pos().y()
        if self._resizing:
            if self._orientation == 'horizontal':
                new_sz = max(self._PROF_MIN, min(self._PROF_MAX, y))
            else:
                new_sz = max(self._PROF_MIN, min(self._PROF_MAX, x))
            self._prof_size = new_sz
            self._update_fixed_size()
            self.update()
            event.accept()
        else:
            if self._on_sep(x, y):
                cur = Qt.CursorShape.SizeVerCursor if self._orientation == 'horizontal' else Qt.CursorShape.SizeHorCursor
                self.setCursor(cur)
            else:
                self.unsetCursor()
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def leaveEvent(self, ev):
        if self._resizing:
            self._resizing = False
            self.unsetCursor()
        super().leaveEvent(ev)

    # ── 페인트 ──────────────────────────────────────────────────────────

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QPen, QFont
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        W, H = self.width(), self.height()
        PR, TK = self._prof_size, self._TICK

        c_bg_prof = QColor(0x0e, 0x16, 0x24)
        c_bg_tick = QColor(0x16, 0x21, 0x3e)
        c_tick    = QColor(0x50, 0x60, 0x80)
        c_text    = QColor(0xa0, 0xa0, 0xb0)
        c_sep     = QColor(0x1a, 0x30, 0x60)
        font      = QFont('Segoe UI', 8)
        p.setFont(font)

        # ── 눈금 간격 ────────────────────────────────────────────
        target_px = 60
        raw_step  = target_px / max(self._scale, 0.001)
        magnitude = 10 ** int(np.log10(max(raw_step, 1)))
        tick_step = magnitude * 10
        for s in [magnitude, magnitude * 2, magnitude * 5, magnitude * 10]:
            if s * self._scale >= target_px:
                tick_step = s
                break
        start_tick = (int(self._offset) // tick_step) * tick_step

        if self._orientation == 'horizontal':
            # 프로파일 영역 (위)
            p.fillRect(0, 0, W, PR, c_bg_prof)
            self._draw_h_profile(p, W, PR)
            p.setPen(QPen(c_sep, 1))
            p.drawLine(0, PR, W, PR)

            # 눈금 영역 (아래)
            p.fillRect(0, PR, W, TK, c_bg_tick)
            p.setPen(QPen(QColor(0x30, 0x40, 0x60), 1))
            p.drawLine(0, PR + TK - 1, W, PR + TK - 1)

            img_px = start_tick
            while True:
                sx = int((img_px - self._offset) * self._scale)
                if sx > W:
                    break
                if img_px >= 0:
                    p.setPen(QPen(c_tick, 1))
                    p.drawLine(sx, PR + TK - 8, sx, PR + TK - 1)
                    p.setPen(c_text)
                    p.drawText(sx + 2, PR + TK - 2, str(img_px))
                img_px += tick_step

        else:  # vertical
            # 프로파일 영역 (왼쪽)
            p.fillRect(0, 0, PR, H, c_bg_prof)
            self._draw_v_profile(p, H, PR)
            p.setPen(QPen(c_sep, 1))
            p.drawLine(PR, 0, PR, H)

            # 눈금 영역 (오른쪽)
            p.fillRect(PR, 0, TK, H, c_bg_tick)
            p.setPen(QPen(QColor(0x30, 0x40, 0x60), 1))
            p.drawLine(PR + TK - 1, 0, PR + TK - 1, H)

            img_px = start_tick
            while True:
                sy = int((img_px - self._offset) * self._scale)
                if sy > H:
                    break
                if img_px >= 0:
                    p.setPen(QPen(c_tick, 1))
                    p.drawLine(PR, sy, PR + 8, sy)
                    p.setPen(c_text)
                    p.save()
                    p.translate(PR + TK - 2, sy - 2)
                    p.rotate(-90)
                    p.drawText(0, 0, str(img_px))
                    p.restore()
                img_px += tick_step

        p.end()

    # ── 프로파일 그리기 ─────────────────────────────────────────────────

    def _draw_h_profile(self, p, W: int, area_h: int):
        """수평 룰러 프로파일 — 열(column) 강도, 높을수록 아래(이미지 방향)."""
        from PyQt6.QtGui import QPen, QColor, QPainterPath
        if self._profile is None or len(self._profile) == 0:
            return
        data = self._profile
        n    = len(data)
        dmin = float(data.min())
        dmax = float(data.max())
        if dmax <= dmin:
            return

        pad  = 3
        span = area_h - 2 * pad
        ps = self._profile_start   # 이미지 좌표 오프셋

        # 화면에 보이는 이미지 좌표 범위
        img_view_lo = self._offset
        img_view_hi = self._offset + W / max(self._scale, 1e-6)
        # 데이터 배열 범위로 변환 (배열 인덱스 = 이미지좌표 - ps)
        d0 = max(0, int(img_view_lo - ps) - 1)
        d1 = min(n - 1, int(img_view_hi - ps) + 1)
        if d0 > d1:
            return

        step = max(1, (d1 - d0) // max(W, 1))

        # 피크 탐색 (보이는 구간)
        visible = data[d0:d1 + 1]
        peak_arr_idx = int(d0 + np.argmax(visible))
        peak_val = float(data[peak_arr_idx])
        peak_img_idx = peak_arr_idx + ps   # 이미지 좌표

        path  = QPainterPath()
        first = True
        for i in range(d0, d1 + 1, step):
            img_idx = i + ps   # 이미지 좌표
            sx = (img_idx - self._offset) * self._scale
            norm = (data[i] - dmin) / (dmax - dmin)
            sy = pad + (1.0 - norm) * span   # 높을수록 위
            if first:
                path.moveTo(sx, sy)
                first = False
            else:
                path.lineTo(sx, sy)

        if not first:
            p.setRenderHint(p.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor('#d4691e'), 1))
            p.drawPath(path)

            # 피크(max) 마커 + 수치
            pk_sx = (peak_img_idx - self._offset) * self._scale
            pk_sy = pad + (1.0 - (peak_val - dmin) / (dmax - dmin)) * span
            p.setPen(QPen(QColor('#ffcc44'), 1))
            p.setBrush(QColor('#ffcc44'))
            p.drawEllipse(int(pk_sx) - 3, int(pk_sy) - 3, 6, 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor('#ffcc44'))
            p.drawText(int(pk_sx) + 5, max(12, int(pk_sy) - 2), f"{peak_val:.0f}")

            # min/max 범위 라벨 (우측 고정)
            fm = p.fontMetrics()
            max_lbl = f"{dmax:.0f}"
            min_lbl = f"{dmin:.0f}"
            max_lbl_w = fm.horizontalAdvance(max_lbl)
            min_lbl_w = fm.horizontalAdvance(min_lbl)
            lbl_x_max = W - max_lbl_w - 2
            lbl_x_min = W - min_lbl_w - 2
            p.setPen(QColor(0xa0, 0xb0, 0xc0))
            p.drawText(lbl_x_max, pad + 10, max_lbl)           # 위 = max (norm=1 → sy=pad)
            p.drawText(lbl_x_min, pad + span - 2, min_lbl)     # 아래 = min (norm=0 → sy=pad+span)

            p.setRenderHint(p.RenderHint.Antialiasing, False)

    def _draw_v_profile(self, p, H: int, area_w: int):
        """수직 룰러 프로파일 — 행(row) 강도, 높을수록 왼쪽(이미지 반대 방향)."""
        from PyQt6.QtGui import QPen, QColor, QPainterPath
        if self._profile is None or len(self._profile) == 0:
            return
        data = self._profile
        n    = len(data)
        dmin = float(data.min())
        dmax = float(data.max())
        if dmax <= dmin:
            return

        pad  = 3
        span = area_w - 2 * pad
        ps = self._profile_start

        img_view_lo = self._offset
        img_view_hi = self._offset + H / max(self._scale, 1e-6)
        d0 = max(0, int(img_view_lo - ps) - 1)
        d1 = min(n - 1, int(img_view_hi - ps) + 1)
        if d0 > d1:
            return

        step = max(1, (d1 - d0) // max(H, 1))

        # 피크 탐색
        visible = data[d0:d1 + 1]
        peak_arr_idx = int(d0 + np.argmax(visible))
        peak_val = float(data[peak_arr_idx])
        peak_img_idx = peak_arr_idx + ps

        path  = QPainterPath()
        first = True
        for i in range(d0, d1 + 1, step):
            img_idx = i + ps
            sy = (img_idx - self._offset) * self._scale
            norm = (data[i] - dmin) / (dmax - dmin)
            sx = pad + (1.0 - norm) * span   # 높을수록 왼쪽
            if first:
                path.moveTo(sx, sy)
                first = False
            else:
                path.lineTo(sx, sy)

        if not first:
            p.setRenderHint(p.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor('#d4691e'), 1))
            p.drawPath(path)

            # 피크(max) 마커 + 수치
            pk_sy = (peak_img_idx - self._offset) * self._scale
            pk_sx = pad + (1.0 - (peak_val - dmin) / (dmax - dmin)) * span
            p.setPen(QPen(QColor('#ffcc44'), 1))
            p.setBrush(QColor('#ffcc44'))
            p.drawEllipse(int(pk_sx) - 3, int(pk_sy) - 3, 6, 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor('#ffcc44'))
            p.drawText(max(2, int(pk_sx) - 30), int(pk_sy) - 4, f"{peak_val:.0f}")

            # min/max 범위 라벨 (상단/하단 고정)
            p.setPen(QColor(0xa0, 0xb0, 0xc0))
            fm = p.fontMetrics()
            max_lbl = f"{dmax:.0f}"
            min_lbl = f"{dmin:.0f}"
            # dmax → sx=pad (왼쪽 끝), dmin → sx=pad+span (오른쪽 끝)
            p.drawText(pad, 12, max_lbl)
            p.drawText(max(pad, area_w - fm.horizontalAdvance(min_lbl) - 2), 12, min_lbl)

            p.setRenderHint(p.RenderHint.Antialiasing, False)
