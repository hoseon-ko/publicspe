from __future__ import annotations
import numpy as np
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ui.viewer_v2.base_view import BaseView
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPen, QColor, QPainter, QFont, QPainterPath
from ui.viewer_v2.viewer_state import ViewerState

class RulerWidgetV2(QWidget):
    """
    개선된 룰러 위젯. 프로파일 그래프를 포함하며 사이즈 변경 시 시그널을 발생시킵니다.
    """
    size_changed = pyqtSignal(int) # 현재 prof_size가 변경됨을 알림

    _TICK = 24
    _PROF_MIN = 20
    _PROF_MAX = 250

    def __init__(self, state: ViewerState, view: BaseView, orientation: str = 'horizontal', parent=None):
        super().__init__(parent)
        self._state = state
        self._view = view
        self._orientation = orientation
        self._prof_size = 60
        self._resizing = False
        
        self.setMouseTracking(True)
        self._update_fixed_size()
        
        # 상태 연결 (화면 변화 시 무조건 다시 그리기)
        self._state.view_transformed.connect(lambda *args: self.update())
        self._state.profile_data_changed.connect(self.update)

    def _update_fixed_size(self):
        total = self._prof_size + self._TICK
        if self._orientation == 'horizontal':
            self.setFixedHeight(total)
        else:
            self.setFixedWidth(total)

    def mousePressEvent(self, event):
        pos = event.pos().y() if self._orientation == 'horizontal' else event.pos().x()
        if abs(pos - self._prof_size) < 5:
            self._resizing = True
            self.setCursor(Qt.CursorShape.SizeVerCursor if self._orientation == 'horizontal' else Qt.CursorShape.SizeHorCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            pos = event.pos().y() if self._orientation == 'horizontal' else event.pos().x()
            self._prof_size = max(self._PROF_MIN, min(self._PROF_MAX, pos))
            self._update_fixed_size()
            self.size_changed.emit(self._prof_size)
            self.update()
        else:
            pos = event.pos().y() if self._orientation == 'horizontal' else event.pos().x()
            if abs(pos - self._prof_size) < 5:
                self.setCursor(Qt.CursorShape.SizeVerCursor if self._orientation == 'horizontal' else Qt.CursorShape.SizeHorCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resizing = False
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        W, H = self.width(), self.height()
        PR, TK = self._prof_size, self._TICK
        scale = self._state.scale
        
        from PyQt6.QtCore import QPointF
        # 이미지 원점(0,0)의 뷰포트 내 실제 픽셀 좌표를 직접 획득
        origin = self._view.mapFromScene(QPointF(0, 0))
        offset = origin.x() if self._orientation == 'horizontal' else origin.y()
        
        c_bg_prof = QColor("#0e1624")
        c_bg_tick = QColor("#16213e")
        c_tick    = QColor("#506080")
        c_text    = QColor("#a0a0b0")
        
        # 1. 배경 채우기
        if self._orientation == 'horizontal':
            p.fillRect(0, 0, W, PR, c_bg_prof)
            p.fillRect(0, PR, W, TK, c_bg_tick)
            p.setPen(QPen(QColor("#1a3060"), 1))
            p.drawLine(0, PR, W, PR)
        else:
            p.fillRect(0, 0, PR, H, c_bg_prof)
            p.fillRect(PR, 0, TK, H, c_bg_tick)
            p.setPen(QPen(QColor("#1a3060"), 1))
            p.drawLine(PR, 0, PR, H)

        # 2. 프로파일 그리기 (데이터 및 라벨 포함)
        data = self._state.x_profile if self._orientation == 'horizontal' else self._state.y_profile
        if data is not None and len(data) > 0:
            self._draw_profile_full(p, data, W, H, PR, scale, offset)

        # 3. 눈금(Ticks) 및 숫자 그리기
        p.setFont(QFont('Segoe UI', 8))
        target_px = 60
        raw_step  = target_px / max(scale, 0.001)
        magnitude = 10 ** int(np.log10(max(raw_step, 1)))
        tick_step = magnitude * 10
        for s in [magnitude, magnitude * 2, magnitude * 5, magnitude * 10]:
            if s * scale >= target_px:
                tick_step = s
                break
        
        # 현재 화면 왼쪽 끝에 해당하는 이미지 픽셀 좌표 역산
        scene_left = -offset / max(scale, 0.001)
        start_tick = (int(scene_left) // tick_step) * tick_step
        img_px = start_tick
        
        while True:
            # 뷰포트 픽셀 좌표 = (0,0)의 위치 + (이미지 좌표 * 배율)
            spos = int(offset + img_px * scale)
            if self._orientation == 'horizontal':
                if spos > W: break
                if spos >= 0:
                    p.setPen(QPen(c_tick, 1))
                    p.drawLine(spos, PR + TK - 8, spos, PR + TK - 1)
                    p.setPen(c_text)
                    p.drawText(spos + 2, PR + TK - 2, str(int(img_px)))
            else:
                if spos > H: break
                if spos >= 0:
                    p.setPen(QPen(c_tick, 1))
                    p.drawLine(PR, spos, PR + 8, spos)
                    p.setPen(c_text)
                    p.save()
                    p.translate(PR + TK - 2, spos - 2)
                    p.rotate(-90)
                    p.drawText(0, 0, str(int(img_px)))
                    p.restore()
            img_px += tick_step
        p.end()

    def _draw_profile_full(self, p: QPainter, data: np.ndarray, W, H, PR, scale, offset):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        n = len(data)
        
        # NaN 제외하고 min/max 계산
        valid_mask = ~np.isnan(data)
        if not np.any(valid_mask): return
        
        dmin, dmax = float(np.nanmin(data)), float(np.nanmax(data))
        if dmax <= dmin: return
        
        # 상하 여백 넉넉히 (수치 표시 공간 확보)
        pad = 15 
        span = PR - 2 * pad
        path = QPainterPath()
        first = True
        
        v_size = W if self._orientation == 'horizontal' else H
        # 가시 범위 인덱스 계산
        scene_start = max(0, int(-offset / max(scale, 0.001)) - 5)
        scene_end = min(n - 1, int((v_size - offset) / max(scale, 0.001)) + 5)

        for i in range(scene_start, scene_end + 1):
            if np.isnan(data[i]):
                first = True
                continue
                
            val = (data[i] - dmin) / (dmax - dmin)
            if self._orientation == 'horizontal':
                sx = offset + i * scale
                sy = pad + (1.0 - val) * span
            else:
                sy = offset + i * scale
                sx = pad + (1.0 - val) * span
            
            if first: 
                path.moveTo(sx, sy)
                first = False
            else: 
                path.lineTo(sx, sy)
        
        p.setPen(QPen(QColor("#d4691e"), 1.2))
        p.drawPath(path)

        # 피크 마커
        visible_data = data[scene_start:scene_end+1]
        v_mask = ~np.isnan(visible_data)
        if np.any(v_mask):
            peak_local_idx = np.nanargmax(visible_data)
            peak_idx = scene_start + peak_local_idx
            peak_val = data[peak_idx]
            
            p.setPen(QColor("#ffcc44"))
            p.setBrush(QColor("#ffcc44"))
            txt = f"{peak_val:.1f}"
            val_norm = (peak_val - dmin) / (dmax - dmin)
            if self._orientation == 'horizontal':
                pk_sx = offset + peak_idx * scale
                pk_sy = pad + (1.0 - val_norm) * span
                p.drawEllipse(int(pk_sx)-3, int(pk_sy)-3, 6, 6)
                
                tx = int(pk_sx) + 5
                ty = int(pk_sy) - 5
                if tx > W - 45: tx = int(pk_sx) - 45
                if ty < 12: ty = int(pk_sy) + 15
                p.drawText(tx, ty, txt)
            else:
                pk_sx = pad + (1.0 - val_norm) * span
                pk_sy = offset + peak_idx * scale
                p.drawEllipse(int(pk_sx)-3, int(pk_sy)-3, 6, 6)
                
                tx = int(pk_sx) - 40
                ty = int(pk_sy) - 5
                if tx < 5: tx = int(pk_sx) + 8
                if ty < 12: ty = int(pk_sy) + 15
                p.drawText(tx, ty, txt)

        # 범위 수치 라벨 (가독성을 위해 숫자만 표시)
        p.setPen(QColor("#a0b0c0"))
        p.setFont(QFont('Segoe UI', 7, QFont.Weight.Bold))
        
        # 그래프 영역 계산 (여백 포함)
        pad = 15
        span = PR - 2 * pad
        
        if self._orientation == 'horizontal':
            # 수평 룰러: 프로파일 영역(PR) 내에서 상/하단 배치
            p.drawText(4, 12, f"{dmax:.0f}")
            p.drawText(4, PR - 4, f"{dmin:.0f}")
        else:
            # 수직 룰러: 프로파일 영역(PR) 내에서 좌/우 배치 (눈금 영역 TK 침범 금지)
            max_txt = f"{dmax:.0f}"
            min_txt = f"{dmin:.0f}"
            tw_min = p.fontMetrics().horizontalAdvance(min_txt)
            
            # 왼쪽 (최대)
            p.drawText(2, 12, max_txt)
            # 오른쪽 (최소) - PR 너비 안에서만 배치
            p.drawText(PR - tw_min - 2, 12, min_txt)
