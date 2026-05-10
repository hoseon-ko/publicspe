
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF
from PyQt6.QtCore import QPointF

class Kinematic3DWidget(QWidget):
    """
    6축 스테이지의 움직임을 3D 와이어프레임으로 투영하여 보여주는 위젯.
    복잡한 라이브러리 없이 순수 QPainter와 투영 행렬을 사용합니다.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        
        # 기하학적 정보
        self.base_points = None # 하단 고정점 (3개 stage x 2 points each = 6 points)
        self.ball_points = None # 상단 볼 위치 (3개 stage x 1 point each = 3 points)
        
        # 뷰 파라미터 (회전각)
        self.yaw = 45.0
        self.pitch = 30.0
        self.zoom = 0.5
        
    def set_geometry(self, base_pts, ball_pts):
        """
        base_pts: np.ndarray[9] (Setup positions)
        ball_pts: np.ndarray[3, 3] (Calculated ball positions)
        """
        self.base_points = base_pts.reshape(3, 3)
        self.ball_points = ball_pts
        self.update()

    def project(self, x, y, z, w, h):
        """3D 좌표를 2D 화면 좌표로 투영 (Isometric/Perspective Projection)"""
        # 1. 원점 조정 (Pivot 기준)
        # 2. 회전 변환
        rad_y = np.radians(self.yaw)
        rad_p = np.radians(self.pitch)
        
        # Yaw
        x1 = x * np.cos(rad_y) - y * np.sin(rad_y)
        y1 = x * np.sin(rad_y) + y * np.cos(rad_y)
        z1 = z
        
        # Pitch
        x2 = x1
        y2 = y1 * np.cos(rad_p) - z1 * np.sin(rad_p)
        z2 = y1 * np.sin(rad_p) + z1 * np.cos(rad_p)
        
        # 화면 중앙으로 이동 및 스케일링
        scale = self.zoom * (min(w, h) / 1000)
        px = w / 2 + x2 * scale
        py = h / 2 - y2 * scale
        
        return QPointF(px, py)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        
        # 배경
        painter.fillRect(self.rect(), QColor("#0d121f"))
        
        if self.base_points is None or self.ball_points is None:
            painter.setPen(QColor("#4a5a70"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No Geometry Data")
            return

        # 투영 포인트 생성
        # base_points는 (3,3) -> 실제로는 6개의 베이스 포인트가 필요함
        # 여기서는 각 스테이지당 2개의 베이스 포인트를 추정하여 6개의 다리를 그림
        ball_pts_2d = [self.project(p[0], p[1], p[2], w, h) for p in self.ball_points]
        
        # 1. 바닥판(Base) 및 6개의 다리 (Legs) 그리기
        painter.setPen(QPen(QColor("#1e293b"), 1, Qt.PenStyle.DashLine))
        
        # 각 볼 조인트(상판)에서 베이스의 두 지점으로 다리가 뻗어나감 (6-SPS 구조)
        # 실제 base_setup 데이터를 기반으로 6개 점을 투영
        base_pts = self.base_points # (3, 3)
        
        leg_pen = QPen(QColor("#00f2ff"), 2)
        leg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(leg_pen)
        
        for i in range(3):
            # 실제 6축은 한 볼에 두 다리가 인접한 베이스 포인트로 연결됨
            # 시각적 완성도를 위해 볼 하나당 두 개의 가상 베이스 포인트를 생성하여 연결
            offset = 30 # 베이스 포인트 간격
            bp1 = self.project(base_pts[i,0]-offset, base_pts[i,1], base_pts[i,2], w, h)
            bp2 = self.project(base_pts[i,0]+offset, base_pts[i,1], base_pts[i,2], w, h)
            
            # 다리 그리기 (Glow 효과를 위해 두 번 그림)
            painter.setPen(QPen(QColor(0, 242, 255, 50), 4))
            painter.drawLine(ball_pts_2d[i], bp1)
            painter.drawLine(ball_pts_2d[i], bp2)
            
            painter.setPen(leg_pen)
            painter.drawLine(ball_pts_2d[i], bp1)
            painter.drawLine(ball_pts_2d[i], bp2)

        # 2. 상판 (Top Plate) 그리기 - 육각형 혹은 원형 느낌으로 보강
        painter.setPen(QPen(QColor("#00f2ff"), 3))
        painter.setBrush(QBrush(QColor(0, 242, 255, 30)))
        
        # 3개의 볼 포인트를 잇는 삼각형 + 외곽 글로우
        poly_top = QPolygonF(ball_pts_2d)
        painter.drawPolygon(poly_top)
        
        # 3. 조인트 포인트 강조
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        painter.setPen(Qt.PenStyle.NoPen)
        for pt in ball_pts_2d:
            painter.drawEllipse(pt, 4, 4)
        
        # 좌표축 표시 (구석에 작게)
        painter.setOpacity(0.5)
        origin = self.project(-400, -400, 0, w, h)
        axis_len = 50
        painter.setPen(QPen(Qt.GlobalColor.red, 1))
        painter.drawLine(origin, self.project(-400+axis_len, -400, 0, w, h)) # X
        painter.setPen(QPen(Qt.GlobalColor.green, 1))
        painter.drawLine(origin, self.project(-400, -400+axis_len, 0, w, h)) # Y
        painter.setPen(QPen(Qt.GlobalColor.blue, 1))
        painter.drawLine(origin, self.project(-400, -400, axis_len, w, h)) # Z
        painter.setOpacity(1.0)
