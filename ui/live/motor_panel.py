"""
ui/live/motor_panel.py
Picomotor 8742 제어 패널 — SpeAnalyze 다크 테마 적용.

가중치 기능:
  Motor 1~3에 전진(+) / 후진(-) 가중치를 각각 설정.
  실제 전송 스텝 = 입력 스텝 × 해당 방향 가중치 (반올림 정수).
  Motor 4는 가중치 없음.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QDoubleSpinBox,
    QFrame, QGroupBox, QGridLayout
)

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from core.config import get_config

from core.motor.picomotor import PicomotorController
from ui.widgets.collapsible_section import CollapsibleSection
from theme.styles import (
    C_ACCENT, C_DANGER, C_WARN, C_BORDER, C_BG_DEEP, C_BG_DARK, C_TEXT, C_TEXT_DIM,
    Fonts, Sizes, BTN_SMALL, SPIN_STYLE, grp_style, lbl
)


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
_GRP_STYLE = """
    QGroupBox {{
        border: 1px solid {color}; border-radius: 6px;
        margin-top: 10px; font-family: 'Courier New';
        font-size: 14px; color: {color};
        letter-spacing: 2px; font-weight: bold;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 단일 모터 카드
# ─────────────────────────────────────────────────────────────────────────────

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
        """#11 이동 명령 시 상태 인디케이터를 노란색으로 잠깐 점등."""
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


# ─────────────────────────────────────────────────────────────────────────────
# 4축 모터 패널
# ─────────────────────────────────────────────────────────────────────────────

class MotorPanel(QWidget):
    """
    Picomotor 8742 4축 제어 패널.

    Motor 1~3 가중치:
      실제_스텝 = round(입력_스텝 × 가중치)
      전진(+), 후진(-) 방향별로 독립 설정.
    """

    connected         = pyqtSignal(str)    # 연결 완료 → 모델명
    disconnected      = pyqtSignal()
    positions_updated = pyqtSignal(list)   # [p1, p2, p3, p4]
    log_message       = pyqtSignal(str)

    # 가중치: {motor_num: (fwd_weight, bwd_weight)}
    # motor 4는 없음
    _WEIGHT_MOTORS = (1, 2, 3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctrl: Optional[PicomotorController] = None
        # 가중치 spinbox 저장: {motor_num: (spin_fwd, spin_bwd)}
        self._weight_spins: Dict[int, Tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}
        # 이동 직전 상태 조회 콜백 — live_tab이 등록, (cx, cy, positions) 반환
        self.pre_move_info_cb = None
        self._build_ui()

    # ── UI 빌드 ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 1. Connection
        self.sec_conn = CollapsibleSection("PICOMOTOR 8742", accent=C_ACCENT)
        self._build_conn_group(self.sec_conn.content_layout())
        layout.addWidget(self.sec_conn)

        # 2. Motor Cards (Axis Controls)
        self.sec_axes = CollapsibleSection("AXIS CONTROLS", accent=C_ACCENT)
        self._build_axes_group(self.sec_axes.content_layout())
        layout.addWidget(self.sec_axes)

        # 3. Weight Settings
        self.sec_weight = CollapsibleSection("STEP WEIGHT SETTINGS", accent=C_WARN, collapsed=True)
        self._build_weight_group(self.sec_weight.content_layout())
        layout.addWidget(self.sec_weight)

        # 섹션 변경 시 자동 저장 연결
        self._sections = [self.sec_conn, self.sec_axes, self.sec_weight]
        for sec in self._sections:
            sec.toggled.connect(self._save_step_settings)

        # ── 포지션 스냅샷 라벨 ────────────────────────────────────────
        self.lbl_snapshot = QLabel("M1:—  M2:—  M3:—  M4:—")
        self.lbl_snapshot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_snapshot.setStyleSheet("""
            color: #ffe66d; font-family: 'Courier New'; font-size: 14px;
            background: #080e1e; border: 1px solid #0f3460;
            border-radius: 4px; padding: 4px;
        """)
        layout.addWidget(self.lbl_snapshot)

        # ── ZERO ALL + STOP ALL ───────────────────────────────────────
        action_row = QHBoxLayout()

        # #17 ZERO ALL
        self.btn_zero_all = QPushButton("⊙  ZERO ALL")
        self.btn_zero_all.setEnabled(False)
        self.btn_zero_all.setFixedHeight(36)
        self.btn_zero_all.setStyleSheet("""
            QPushButton { background: #1a1020; color: #ffe66d; border: 1px solid #6a5010;
                font-family: 'Courier New'; font-weight: bold; font-size: 14px;
                letter-spacing: 1px; border-radius: 4px; }
            QPushButton:hover { background: #2a2030; }
            QPushButton:disabled { color: #2a2010; background: #100808; border-color: #1a1008; }
        """)
        action_row.addWidget(self.btn_zero_all)

        self.btn_stop_all = QPushButton("⛔  STOP ALL")
        self.btn_stop_all.setEnabled(False)
        self.btn_stop_all.setFixedHeight(36)
        self.btn_stop_all.setStyleSheet("""
            QPushButton { background: #200808; color: #ff3322; border: 2px solid #aa2211;
                font-family: 'Courier New'; font-weight: bold; font-size: 14px;
                letter-spacing: 1px; border-radius: 4px; }
            QPushButton:hover { background: #380e0e; }
            QPushButton:disabled { color: #2a1010; background: #100404; border-color: #200808; }
        """)
        action_row.addWidget(self.btn_stop_all)
        layout.addLayout(action_row)
        layout.addStretch()

        # 시그널 연결
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_zero_all.clicked.connect(self._on_zero_all)
        self.btn_stop_all.clicked.connect(self._on_stop_all)

        # #18 저장된 스텝 값 복원
        self._restore_step_settings()

    def _build_conn_group(self, lay: QVBoxLayout):
        lay.setSpacing(6)
        lay.setContentsMargins(4, 4, 4, 4)

        row_btn = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setStyleSheet(BTN_SMALL)
        
        self.btn_disconnect = QPushButton("DISCONNECT")
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.setStyleSheet(BTN_SMALL.replace(C_ACCENT, C_DANGER))
        
        row_btn.addWidget(self.btn_connect)
        row_btn.addWidget(self.btn_disconnect)
        lay.addLayout(row_btn)

        self.lbl_status = QLabel("● DISCONNECTED")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        lay.addWidget(self.lbl_status)

    def _build_axes_group(self, lay: QVBoxLayout):
        grid = QGridLayout()
        grid.setSpacing(6)
        self.motor_cards: List[MotorCard] = []
        for i in range(1, 5):
            card = MotorCard(i)
            card.move_requested.connect(self._on_move_requested)
            self.motor_cards.append(card)
            grid.addWidget(card, (i-1)//2, (i-1)%2)
        lay.addLayout(grid)

    def _build_weight_group(self, lay: QVBoxLayout):
        lay.setSpacing(5)


        # 헤더 행
        hdr = QHBoxLayout()
        for text, width in [("Motor", 55), ("+ FWD", 85), ("− BWD", 85)]:
            lbl = QLabel(text)
            lbl.setFixedWidth(width)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {C_WARN}; font-family: '{Fonts.MONO}'; font-size: 13px; font-weight: bold;"
            )
            hdr.addWidget(lbl)
        hdr.addStretch()
        lay.addLayout(hdr)


        # M1~M3 각각 한 행
        for m in self._WEIGHT_MOTORS:
            row = QHBoxLayout()
            row.setSpacing(4)

            lbl_m = QLabel(f"M{m}")
            lbl_m.setFixedWidth(55)
            lbl_m.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_m.setStyleSheet(
                "color: #4ecdc4; font-family: 'Courier New'; font-weight: bold; font-size: 15px;"
            )

            spin_fwd = QDoubleSpinBox()
            spin_fwd.setRange(0.01, 100.0)
            spin_fwd.setSingleStep(0.1)
            spin_fwd.setDecimals(3)
            spin_fwd.setValue(1.0)
            spin_fwd.setFixedWidth(85)
            spin_fwd.setStyleSheet(_SPIN_STYLE)
            spin_fwd.setToolTip(f"Motor {m} 전진(+) 가중치\n실제 스텝 = 입력 × 가중치")

            spin_bwd = QDoubleSpinBox()
            spin_bwd.setRange(0.01, 100.0)
            spin_bwd.setSingleStep(0.1)
            spin_bwd.setDecimals(3)
            spin_bwd.setValue(1.0)
            spin_bwd.setFixedWidth(85)
            spin_bwd.setStyleSheet(_SPIN_STYLE)
            spin_bwd.setToolTip(f"Motor {m} 후진(-) 가중치\n실제 스텝 = 입력 × 가중치")

            # 미리보기 라벨 (입력 100 기준)
            lbl_preview = QLabel("→100")
            lbl_preview.setStyleSheet(
                "color: #4a5a7a; font-family: 'Courier New'; font-size: 13px;"
            )

            spin_fwd.setStyleSheet(SPIN_STYLE)
            spin_bwd.setStyleSheet(SPIN_STYLE)
            self._weight_spins[m] = (spin_fwd, spin_bwd)

            row.addWidget(lbl_m)
            row.addWidget(spin_fwd)
            row.addWidget(spin_bwd)
            row.addStretch()
            lay.addLayout(row)


        # 리셋 버튼
        btn_reset_w = QPushButton("Reset all weights → 1.0")
        btn_reset_w.setStyleSheet("""
            QPushButton { background: #111a30; color: #ffe66d; border: 1px solid #3a3010;
                border-radius: 3px; font-family: 'Courier New'; font-size: 13px; padding: 3px 6px; }
            QPushButton:hover { background: #1e2a10; }
        """)
        btn_reset_w.clicked.connect(self._reset_weights)
        lay.addWidget(btn_reset_w)

    # ── 가중치 헬퍼 ───────────────────────────────────────────────────

    def _get_weight(self, motor_num: int, forward: bool) -> float:
        """motor_num(1~3)의 해당 방향 가중치 반환. 4번은 항상 1.0."""
        if motor_num not in self._weight_spins:
            return 1.0
        spin_fwd, spin_bwd = self._weight_spins[motor_num]
        return spin_fwd.value() if forward else spin_bwd.value()

    def _apply_weight(self, motor_num: int, steps: int) -> int:
        """steps에 가중치를 곱해 실제 전송할 스텝 수를 반환."""
        if steps == 0:
            return 0
        weight = self._get_weight(motor_num, forward=(steps > 0))
        return int(round(abs(steps) * weight)) * (1 if steps > 0 else -1)

    def _reset_weights(self):
        for spin_fwd, spin_bwd in self._weight_spins.values():
            spin_fwd.setValue(1.0)
            spin_bwd.setValue(1.0)
        self.log_message.emit("가중치 초기화 → 1.0")

    def get_weights(self) -> Dict[int, Tuple[float, float]]:
        """현재 가중치 딕셔너리 반환 {motor: (fwd, bwd)}."""
        return {
            m: (sf.value(), sb.value())
            for m, (sf, sb) in self._weight_spins.items()
        }

    def set_weights(self, weights: Dict[int, Tuple[float, float]]):
        """외부에서 가중치 설정. {motor: (fwd, bwd)}."""
        for m, (fwd, bwd) in weights.items():
            if m in self._weight_spins:
                sf, sb = self._weight_spins[m]
                sf.setValue(fwd)
                sb.setValue(bwd)

    # ── 연결 ─────────────────────────────────────────────────────────

    def _on_connect(self):
        try:
            self._ctrl = PicomotorController()
            model = self._ctrl.connect()
            self.lbl_status.setText(f"● {model}")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.btn_stop_all.setEnabled(True)
            self.btn_zero_all.setEnabled(True)   # #17
            for card in self.motor_cards:
                card.set_enabled(True)

            self._ctrl.start_polling(self._on_positions, self._on_connection_lost)
            self.connected.emit(model)
            self.log_message.emit(f"✅ Picomotor 연결: {model}")
        except Exception as e:
            self._ctrl = None
            self.log_message.emit(f"❌ 연결 오류: {e}")

    def _on_disconnect(self):
        if self._ctrl:
            self._ctrl.disconnect()
            self._ctrl = None
        self._reset_ui()
        self.disconnected.emit()
        self.log_message.emit("Picomotor 연결 해제")

    def _reset_ui(self):
        self.lbl_status.setText("● DISCONNECTED")
        self.lbl_status.setStyleSheet(lbl(C_DANGER, mono=True, bold=True))
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.btn_stop_all.setEnabled(False)
        self.btn_zero_all.setEnabled(False)   # #17
        for card in self.motor_cards:
            card.set_enabled(False)
        self.lbl_snapshot.setText("M1:—  M2:—  M3:—  M4:—")

    # ── 폴링 콜백 ─────────────────────────────────────────────────────

    def _on_positions(self, positions: list):
        for i, card in enumerate(self.motor_cards):
            card.set_position(positions[i] if i < len(positions) else None)
        parts = [
            f"M{i+1}:{f'{p:,}' if p is not None else '—'}"
            for i, p in enumerate(positions[:4])
        ]
        self.lbl_snapshot.setText("  ".join(parts))
        self.positions_updated.emit(positions)

    def _on_connection_lost(self):
        """폴링 워커가 연결 끊김을 감지 → ctrl 정리 후 UI 초기화."""
        self.log_message.emit("⚠️ Picomotor 연결 끊김")
        # 폴링 워커는 이미 루프를 빠져나왔으므로 stop_polling()만 호출
        if self._ctrl:
            try:
                self._ctrl.stop_polling()
            except Exception:
                pass
            self._ctrl = None
        self._reset_ui()
        self.disconnected.emit()

    # ── 이동 (가중치 적용) ────────────────────────────────────────────

    def _on_move_requested(self, motor_num: int, steps: int):
        if self._ctrl is None or not self._ctrl.is_connected:
            return

        # 이동 전 상태 로그 — centroid + 현재 모터 위치
        if self.pre_move_info_cb is not None:
            try:
                cx, cy, positions = self.pre_move_info_cb()
                cx_s = f"{cx:.2f}" if cx is not None else "N/A"
                cy_s = f"{cy:.2f}" if cy is not None else "N/A"
                pos_s = "  ".join(
                    f"M{i+1}={p if p is not None else '?'}"
                    for i, p in enumerate(positions)
                )
                actual = self._apply_weight(motor_num, steps) if steps != 0 else 0
                sign = "+" if actual >= 0 else ""
                self.log_message.emit(
                    f"▶ M{motor_num} 이동 예정 {sign}{actual}steps  |  "
                    f"Centroid=({cx_s}, {cy_s})  |  {pos_s}"
                )
            except Exception:
                pass

        # #11 이동 피드백: 카드 인디케이터 점등
        card = self.motor_cards[motor_num - 1]
        card.flash_moving(steps)
        try:
            if steps == 0:
                self._ctrl.zero(motor_num)
                self.log_message.emit(f"Motor {motor_num}: ZERO set")
            else:
                raw_steps     = steps
                actual_steps  = self._apply_weight(motor_num, steps)
                ok = self._ctrl.move_relative(motor_num, actual_steps)
                sign = "+" if actual_steps > 0 else ""
                w = self._get_weight(motor_num, forward=(steps > 0))
                weight_note = f"  [w={w:.3f}: {raw_steps}→{actual_steps}]" if motor_num in self._weight_spins else ""
                self.log_message.emit(
                    f"Motor {motor_num}: {sign}{actual_steps} steps{weight_note} → {'OK' if ok else 'FAIL'}"
                )
        except Exception as e:
            self.log_message.emit(f"Motor {motor_num} 오류: {e}")

    def _on_zero_all(self):
        """#17 모든 축 포지션 카운터 초기화 (물리 이동 없음)."""
        if self._ctrl is None or not self._ctrl.is_connected:
            return
        errors = []
        for i in range(1, 5):
            try:
                self._ctrl.zero(i)
            except Exception as e:
                errors.append(f"M{i}:{e}")
        if errors:
            self.log_message.emit(f"⚠️ ZERO ALL 일부 실패: {', '.join(errors)}")
        else:
            self.log_message.emit("⊙ 전체 포지션 카운터 초기화 완료")

    def _on_stop_all(self):
        if self._ctrl:
            try:
                self._ctrl.stop_all()
                self.log_message.emit("⛔ 전체 모터 정지")
            except Exception as e:
                self.log_message.emit(f"정지 오류: {e}")

    # ── DeepAlign Master Bar 공개 API ─────────────────────────────────────────

    def zero_all(self) -> None:
        """모든 축 포지션 카운터 초기화 (물리 이동 없음). Master Bar에서 호출."""
        self._on_zero_all()

    def reset_controller(self) -> None:
        """컨트롤러 연결 끊고 재연결 (현재: disconnect만). Master Bar에서 호출."""
        if self._ctrl is None:
            return
        try:
            self._ctrl.stop_all()
        except Exception:
            pass

    def stop_all(self) -> None:
        """모든 축 즉시 정지. Master Bar에서 호출."""
        self._on_stop_all()

    def stop_polling(self):
        """종료 시 컨트롤러 폴링 및 연결 정지."""
        if self._ctrl:
            try:
                self._ctrl.stop_polling()
                self._ctrl.disconnect()
            except Exception:
                pass

    # ── #18 스텝 값 영속화 ────────────────────────────────────────────

    def _restore_step_settings(self):
        """앱 재시작 시 마지막 스텝 값 복원."""
        c = get_config()
        for i, card in enumerate(self.motor_cards, 1):
            val = c.get(f"devices.picomotor.motors.m{i}.step", card.spin.value())
            try:
                card.spin.setValue(int(val))
            except (TypeError, ValueError):
                pass
        for m, (sf, sb) in self._weight_spins.items():
            fwd = c.get(f"devices.picomotor.motors.m{m}.weight_fwd", sf.value())
            bwd = c.get(f"devices.picomotor.motors.m{m}.weight_bwd", sb.value())
            try:
                sf.setValue(float(fwd)); sb.setValue(float(bwd))
            except (TypeError, ValueError):
                pass

        for sec in self._sections:
            name = sec._title_lbl.text().replace(' ', '_').lower()
            val = c.get(f"ui.sections_collapsed.{name}", None)
            if val is not None:
                sec.set_collapsed(bool(val))

    def _save_step_settings(self):
        """스텝 값 + 가중치를 설정 파일에 저장."""
        c = get_config()
        for i, card in enumerate(self.motor_cards, 1):
            c.set(f"devices.picomotor.motors.m{i}.step", int(card.spin.value()))
        for m, (sf, sb) in self._weight_spins.items():
            c.set(f"devices.picomotor.motors.m{m}.weight_fwd", float(sf.value()))
            c.set(f"devices.picomotor.motors.m{m}.weight_bwd", float(sb.value()))

        for sec in self._sections:
            name = sec._title_lbl.text().replace(' ', '_').lower()
            c.set(f"ui.sections_collapsed.{name}", sec.is_collapsed())

        c.save()

    # ── Public ────────────────────────────────────────────────────────

    def get_positions(self) -> list:
        """현재 포지션 즉시 조회 (저장 등 일회성 용도)."""
        if self._ctrl and self._ctrl.is_connected:
            try:
                return self._ctrl.get_all_positions()
            except Exception:
                pass
        return [None, None, None, None]

    def move(self, motor_num: int, steps: int) -> bool:
        """외부(ScanWorker 등 백그라운드 스레드)에서 모터 이동 명령.
        flash_moving()은 UI 조작이므로 여기서 호출하지 않는다."""
        if self._ctrl is None or not self._ctrl.is_connected:
            return False
        try:
            if steps == 0:
                self._ctrl.zero(motor_num)
                return True
            actual = self._apply_weight(motor_num, steps)
            return bool(self._ctrl.move_relative(motor_num, actual))
        except Exception as e:
            self.log_message.emit(f"Motor {motor_num} 이동 오류: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._ctrl is not None and self._ctrl.is_connected

    @property
    def controller(self) -> Optional[PicomotorController]:
        return self._ctrl

    def set_controller(self, ctrl: PicomotorController):
        """외부에서 생성된 컨트롤러 주입 (DeepAlign 탭 등에서 공유 용도)."""
        if ctrl is None:
            return
        self._ctrl = ctrl
        if self._ctrl.is_connected:
            model = self._ctrl.model_name or "Connected"
            self.lbl_status.setText(f"● {model}")
            self.lbl_status.setStyleSheet(lbl(C_ACCENT, mono=True, bold=True))
            self.btn_connect.setEnabled(False)
            self._set_enabled(True)
            self._start_polling()
            self.connected.emit(model)

    def cleanup(self):
        self._save_step_settings()   # #18
        if self._ctrl:
            self._ctrl.disconnect()
            self._ctrl = None
