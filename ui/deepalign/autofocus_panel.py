"""
ui/deepalign/autofocus_panel.py
KIMM Z 스테이지 제어 + Best-Z 결과 표시 패널 (간소화 버전).

기존 패널의 Z SCAN RANGE / SHARPNESS METRIC / RUN-STOP / progress /
SHARPNESS CURVE 5섹션은 KimmScanWidget + analysis dock 가 모두 커버하므로
제거. 이 패널에 남는 것:
  - KimmZCard (KIMM Z 스테이지 제어 — IP/Port 연결, 수동 jog, GoToZ)
  - RESULT (Best Z µm + GO 버튼) — 스캔 종료 후 main_tab 가 set_result 로 채움

스캔 자체는 KimmScanWidget 이 트리거. main_tab 이 스캔 finished 시점에
sharpness 시계열의 argmax 를 Best-Z 로 산출해 set_result 호출.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal

from theme.styles import Fonts, Sizes, C_ACCENT, C_BORDER, C_TEXT_DIM
from ui.widgets.kimm_z_card import KimmZCard

_FC  = Fonts.MONO
_FS  = Sizes.CTRL
_FSS = Sizes.SMALL

_LBL_QSS = f"color: {C_TEXT_DIM}; font-family: '{_FC}'; font-size: {_FSS};"


def _btn_qss(color: str) -> str:
    return (
        f"QPushButton {{"
        f"  background: transparent; color: {color};"
        f"  border: 1px solid {color}; border-radius: 3px;"
        f"  font-family: '{_FC}'; font-size: {_FS};"
        f"  font-weight: bold; padding: 4px 10px;"
        f"}}"
        f"QPushButton:hover {{ background: {color}22; }}"
        f"QPushButton:disabled {{ color: #304060; border-color: #1a2840; }}"
    )


def _sep_h() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {C_BORDER}; margin: 2px 0;")
    return f


class AutoFocusPanel(QWidget):
    """KIMM Z 제어 카드 + Best-Z 결과 표시.

    공개 API (이전 버전 호환):
      - set_result(best_z): Best Z 표시 + GO 활성
      - bind_session_hub(hub): KimmZCard 에 위임 + GO 버튼이 사용할 hub 보관
      - reset(): Best-Z 초기화

      [no-op stubs for backward-compat — 마스터 바가 아직 호출할 수 있음]
      - run_af / abort_af / set_z_base / update_progress / set_error
    """

    # 옛 시그널 보존 (외부 connect 가 끊기지 않도록) — 실제 emit 안 함
    run_requested  = pyqtSignal(float, float, float, str)
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._session_hub = None
        self._best_z: float | None = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        # ── KIMM Z 스테이지 제어 (공통 위젯) ─────────────────────────
        self.kimm_card = KimmZCard()
        root.addWidget(self.kimm_card)

        root.addSpacing(4)
        root.addWidget(_sep_h())

        # ── RESULT — Best Z (KimmScanWidget 스캔 후 main_tab 가 채움) ──
        sec_lbl = QLabel("RESULT")
        sec_lbl.setStyleSheet(
            f"color: #2a4a6a; font-family: '{_FC}'; font-size: {_FSS};"
            f" font-weight: bold; letter-spacing: 2px;"
        )
        root.addWidget(sec_lbl)

        result_row = QHBoxLayout()
        lbl_best = QLabel("Best Z")
        lbl_best.setStyleSheet(_LBL_QSS)

        self.lbl_best_z = QLabel("—  µm")
        self.lbl_best_z.setStyleSheet(
            f"color: #e94560; font-family: '{_FC}'; font-size: 18px;"
            f" font-weight: bold;"
        )
        self.lbl_best_z.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_goto = QPushButton("GO")
        self.btn_goto.setFixedWidth(42)
        self.btn_goto.setToolTip("Best Z 위치로 이동")
        self.btn_goto.setStyleSheet(_btn_qss("#e94560"))
        self.btn_goto.setEnabled(False)
        self.btn_goto.clicked.connect(self._on_goto_best)

        result_row.addWidget(lbl_best)
        result_row.addWidget(self.lbl_best_z, 1)
        result_row.addWidget(self.btn_goto)
        root.addLayout(result_row)

        root.addStretch(1)

    # ── 외부 API ──────────────────────────────────────────────────────────

    def bind_session_hub(self, hub):
        self._session_hub = hub
        self.kimm_card.bind_session_hub(hub)

    def set_result(self, best_z: float):
        """스캔 종료 시 main_tab 이 호출 — Best Z 표시 + GO 활성."""
        if best_z is None:
            return
        self._best_z = float(best_z)
        self.lbl_best_z.setText(f"{float(best_z):+.2f}  µm")
        self.btn_goto.setEnabled(True)

    def reset(self):
        self._best_z = None
        self.lbl_best_z.setText("—  µm")
        self.btn_goto.setEnabled(False)

    @property
    def best_z(self) -> float | None:
        return self._best_z

    def _on_goto_best(self):
        """GO 버튼 — Best-Z 위치로 hub.kimm_move_to_z 호출 (비차단)."""
        if self._best_z is None or self._session_hub is None:
            return
        import threading
        target_val = float(self._best_z)
        def run():
            try:
                self._session_hub.kimm_move_to_z(target_val)
            except Exception as e:
                from core.logger import dev_logger
                dev_logger.warning(f"[AutoFocusPanel] GO 이동 실패: {e}")
        threading.Thread(target=run, daemon=True).start()

    # ── 옛 마스터 바 호출 호환 stub (no-op) ────────────────────────────

    def run_af(self) -> None:
        """Deprecated — KimmScanWidget 의 SCAN START 사용."""
        from core.logger import dev_logger
        dev_logger.info("[AutoFocusPanel] run_af 호출됨 (deprecated) — "
                        "KimmScanWidget 의 SCAN START 를 사용하세요.")

    def abort_af(self) -> None:
        from core.logger import dev_logger
        dev_logger.info("[AutoFocusPanel] abort_af 호출됨 (deprecated) — "
                        "KimmScanWidget 의 SCAN STOP 을 사용하세요.")

    def set_z_base(self) -> None:
        from core.logger import dev_logger
        dev_logger.info("[AutoFocusPanel] set_z_base 호출됨 (현재 no-op).")

    def update_progress(self, step: int, total: int, z: float, sharpness: float):
        # 옛 AutoFocusWorker → set_result 만 사용. 이 메서드는 no-op.
        pass

    def set_error(self, msg: str):
        # 마지막 결과 라벨에 에러만 표시
        self.lbl_best_z.setText("ERR")
