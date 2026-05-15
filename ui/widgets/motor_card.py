"""
ui/widgets/motor_card.py
Picomotor 단일 축 제어용 카드 위젯.
DeepAlign 및 Motion 탭에서 공통으로 사용됩니다.
"""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

# ── 공통 스타일 ────────────────────────────────────────────────────────────────
_CARD_STYLE = """
    QFrame#motorCard {
        background: #0f1729;
        border: 1px solid #0f3460;
        border-radius: 6px;
    }
"""
_SPIN_STYLE = """
    QSpinBox, QDoubleSpinBox {
        background: #080e1e; border: 1px solid #0f3460;
        color: #c0d0ff; border-radius: 3px;
        font-family: 'Courier New'; font-size: 14px; padding: 2px 4px;
    }
"""


class MotorCard(QFrame):
    """1개 축의 포지션 표시 + 스텝 이동 컨트롤."""

    move_requested = pyqtSignal(int, int)   # (motor_num, steps); steps==0 → ZERO

    def __init__(self, motor_num: int, parent=None):
        super().__init__(parent)
        self.motor_num = motor_num
        self.setObjectName("motorCard")
        self.setStyleSheet(_CARD_STYLE)
        self._build_ui()
        self.set_enabled(False)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # 헤더: 번호 + 상태 점
        top = QHBoxLayout()
        lbl_num = QLabel(f"M{self.motor_num}")
        lbl_num.setStyleSheet(
            "color: #4ecdc4; font-family: 'Courier New'; font-weight: bold; font-size: 15px;"
        )
        self.lbl_dot = QLabel("●")
        self.lbl_dot.setStyleSheet("color: #2a3a6a; font-family: 'Courier New'; font-size: 13px;")
        top.addWidget(lbl_num)
        top.addStretch()
        top.addWidget(self.lbl_dot)
        layout.addLayout(top)

        # 포지션
        self.lbl_pos = QLabel("—")
        self.lbl_pos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_pos.setStyleSheet("""
            color: #e0e8ff; font-family: 'Courier New'; font-size: 21px; font-weight: bold;
            background: #080e1e; border: 1px solid #0f3460; border-radius: 4px; padding: 3px;
        """)
        layout.addWidget(self.lbl_pos)

        # 스텝 입력
        step_row = QHBoxLayout()
        lbl_s = QLabel("Steps")
        lbl_s.setStyleSheet("color: #3a5080; font-size: 13px; font-family: 'Courier New';")
        self.spin = QSpinBox()
        self.spin.setRange(-999999, 999999)
        self.spin.setValue(100)
        self.spin.setFixedWidth(85)
        self.spin.setStyleSheet(_SPIN_STYLE)
        step_row.addWidget(lbl_s)
        step_row.addWidget(self.spin)
        layout.addLayout(step_row)

        # 빠른 스텝 선택
        quick = QHBoxLayout()
        quick.setSpacing(3)
        for v in [10, 50, 100, 500]:
            b = QPushButton(str(v))
            b.setFixedHeight(20)
            b.setStyleSheet("""
                QPushButton {
                    background: #111a30; color: #3a5080;
                    border: 1px solid #0f3460; border-radius: 3px;
                    font-size: 13px; font-family: 'Courier New'; padding: 0;
                }
                QPushButton:hover { color: #4ecdc4; border-color: #4ecdc4; }
            """)
            b.clicked.connect(lambda _, val=v: self.spin.setValue(val))
            quick.addWidget(b)
        layout.addLayout(quick)

        # 이동 버튼
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_neg = QPushButton("◀ −")
        self.btn_neg.setStyleSheet("""
            QPushButton { background: #0d1e38; color: #4ecdc4; border: 1px solid #1a4060;
                border-radius: 4px; font-family: 'Courier New'; font-weight: bold; }
            QPushButton:hover { background: #1a3a60; }
            QPushButton:disabled { color: #1a2840; background: #080e1e; border-color: #0a1828; }
        """)
        self.btn_pos = QPushButton("+ ▶")
        self.btn_pos.setStyleSheet("""
            QPushButton { background: #0d2820; color: #4ecdc4; border: 1px solid #1a5040;
                border-radius: 4px; font-family: 'Courier New'; font-weight: bold; }
            QPushButton:hover { background: #1a4838; }
            QPushButton:disabled { color: #1a2840; background: #080e1e; border-color: #0a1828; }
        """)
        self.btn_zero = QPushButton("ZERO")
        self.btn_zero.setStyleSheet("""
            QPushButton { background: #281020; color: #e94560; border: 1px solid #5a2040;
                border-radius: 4px; font-family: 'Courier New'; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background: #3a1830; }
            QPushButton:disabled { color: #1a2840; background: #080e1e; border-color: #0a1828; }
        """)
        btn_row.addWidget(self.btn_neg)
        btn_row.addWidget(self.btn_pos)
        btn_row.addWidget(self.btn_zero)
        layout.addLayout(btn_row)

        self.btn_neg.clicked.connect(lambda: self.move_requested.emit(self.motor_num, -self.spin.value()))
        self.btn_pos.clicked.connect(lambda: self.move_requested.emit(self.motor_num, self.spin.value()))
        self.btn_zero.clicked.connect(lambda: self.move_requested.emit(self.motor_num, 0))

    def set_position(self, pos: Optional[int]):
        if pos is None:
            self.lbl_pos.setText("ERR")
            self.lbl_dot.setText("●")
            self.lbl_dot.setStyleSheet("color: #e94560; font-family: 'Courier New'; font-size: 13px;")
        else:
            self.lbl_pos.setText(f"{pos:,}")
            self.lbl_dot.setText("●")   # flash_moving 후 텍스트 복원
            self.lbl_dot.setStyleSheet("color: #4ecdc4; font-family: 'Courier New'; font-size: 13px;")

    def flash_moving(self, steps: int):
        """이동 명령 시 상태 인디케이터를 노란색으로 잠깐 점등."""
        dir_str = "→" if steps > 0 else ("←" if steps < 0 else "⊙")
        self.lbl_dot.setText(f"{dir_str}")
        self.lbl_dot.setStyleSheet("color: #ffe66d; font-family: 'Courier New'; font-size: 13px;")
        # 1.5초 후 복원 (폴링으로 위치 갱신되면 자동으로 teal 복구)
        QTimer.singleShot(1500, self._reset_dot)

    def _reset_dot(self):
        self.lbl_dot.setText("●")
        self.lbl_dot.setStyleSheet("color: #4ecdc4; font-family: 'Courier New'; font-size: 13px;")

    def set_enabled(self, en: bool):
        for w in (self.btn_neg, self.btn_pos, self.btn_zero, self.spin):
            w.setEnabled(en)
        if not en:
            self.lbl_pos.setText("—")
            self.lbl_dot.setText("●")
            self.lbl_dot.setStyleSheet("color: #2a3a6a; font-family: 'Courier New'; font-size: 13px;")
