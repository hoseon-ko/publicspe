"""
theme/styles.py
디자인 토큰 — SpeAnalyze 전체 탭이 사용하는 단일 스타일 출처.

사용법:
    from theme.styles import C_ACCENT, BTN_PRIMARY, grp_style, Fonts, Sizes

    # GroupBox
    box = QGroupBox("CAMERA")
    box.setStyleSheet(grp_style())          # 기본 빨강 타이틀
    box.setStyleSheet(grp_style(C_ACCENT))  # 청록 타이틀

    # 버튼
    btn.setStyleSheet(BTN_PRIMARY)
    btn.setStyleSheet(BTN_DANGER)

    # 인라인 레이블
    label.setStyleSheet(lbl())                        # 기본 흐린 텍스트
    label.setStyleSheet(lbl(C_ACCENT, mono=True))     # 청록 모노
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# 색상 팔레트
# ─────────────────────────────────────────────────────────────────────────────

# 배경 계층 (어두울수록 더 깊은 레이어)
C_BG_DEEP   = "#080e1e"   # 최심부 — 입력 필드, 텍스트에디트, 뷰어
C_BG_DARK   = "#0a1020"   # 탭/패널 배경
C_BG_MED    = "#0f1729"   # 카드·그룹 내부
C_BG_LIGHT  = "#16213e"   # 상단바·도크 타이틀·팝업

# 테두리
C_BORDER    = "#0f3460"   # 기본 테두리
C_BORDER_HI = "#1a3a60"   # 호버·강조 테두리

# 텍스트
C_TEXT      = "#c0d0ff"   # 기본 입력값 (밝은 청백)
C_TEXT_DIM  = "#8090b0"   # 보조 레이블 (흐림)
C_TEXT_DEAD = "#4a6a8a"   # 비활성·힌트 (매우 흐림)
C_TEXT_LOG  = "#00cc88"   # 로그 기본 (초록)

# 기능 색상 (Accent)
C_ACCENT    = "#4ecdc4"   # 주 액션 — 버튼·하이라이트·활성 상태
C_WARN      = "#ffe66d"   # 경고·ETA·진행 중
C_DANGER    = "#e94560"   # 오류·위험·그룹 타이틀 강조
C_INFO      = "#a0c8ff"   # 정보·온도 수치

# 버튼 배경
C_ACCENT_BG = "#0d2820"   # 주 액션 버튼 배경
C_DANGER_BG = "#200808"   # 위험 버튼 배경


# ─────────────────────────────────────────────────────────────────────────────
# 폰트
# ─────────────────────────────────────────────────────────────────────────────

class Fonts:
    UI   = "Segoe UI"       # 레이블·버튼·체크박스
    MONO = "Courier New"    # 수치·로그·스핀박스 값


# ─────────────────────────────────────────────────────────────────────────────
# 폰트 사이즈
# ─────────────────────────────────────────────────────────────────────────────

class Sizes:
    TITLE  = "16px"   # 탭/패널 제목
    BTN    = "14px"   # 주요 버튼
    CTRL   = "12px"   # 컨트롤 레이블·스핀박스·콤보
    LOG    = "11px"   # 로그·테이블 텍스트
    SMALL  = "10px"   # 보조 레이블·상태바 힌트


# ─────────────────────────────────────────────────────────────────────────────
# 버튼 QSS
# ─────────────────────────────────────────────────────────────────────────────

BTN_PRIMARY = (
    f"QPushButton {{"
    f"  background: {C_ACCENT_BG}; color: {C_ACCENT};"
    f"  border: 1px solid {C_ACCENT}; border-radius: 4px;"
    f"  font-family: '{Fonts.UI}'; font-weight: bold;"
    f"  font-size: {Sizes.BTN}; padding: 6px 12px;"
    f"}}"
    f"QPushButton:hover {{ background: #1a4838; }}"
    f"QPushButton:disabled {{"
    f"  color: #1a2840; background: {C_BG_DEEP}; border-color: #0a1828;"
    f"}}"
)

BTN_DANGER = (
    f"QPushButton {{"
    f"  background: {C_DANGER_BG}; color: {C_DANGER};"
    f"  border: 1px solid {C_DANGER}; border-radius: 4px;"
    f"  font-family: '{Fonts.UI}'; font-weight: bold;"
    f"  font-size: {Sizes.BTN}; padding: 6px 12px;"
    f"}}"
    f"QPushButton:hover {{ background: #3a1020; }}"
    f"QPushButton:disabled {{"
    f"  color: #2a1010; background: #100404; border-color: {C_DANGER_BG};"
    f"}}"
)

# 토글 가능한 플랫 버튼 (원본/이진화, Show A/B 등)
BTN_FLAT = (
    f"QPushButton {{"
    f"  background: {C_BG_MED}; color: {C_TEXT_DIM};"
    f"  border: 1px solid {C_BORDER}; border-radius: 3px;"
    f"  font-family: '{Fonts.UI}'; font-size: {Sizes.CTRL};"
    f"  padding: 4px 10px; min-width: 52px;"
    f"}}"
    f"QPushButton:hover {{ color: {C_TEXT}; }}"
    f"QPushButton:checked {{"
    f"  background: {C_ACCENT_BG}; color: {C_ACCENT}; border-color: {C_ACCENT};"
    f"}}"
    f"QPushButton:disabled {{ color: #1a2840; background: {C_BG_DEEP}; }}"
)

# SIM 모드 전용 노랑 버튼
BTN_SIM = (
    f"QPushButton {{"
    f"  background: #1a1a0a; color: {C_WARN};"
    f"  border: 1px solid {C_WARN}; border-radius: 4px;"
    f"  font-family: '{Fonts.UI}'; font-weight: bold;"
    f"  font-size: {Sizes.BTN}; padding: 6px 10px;"
    f"}}"
    f"QPushButton:hover {{ background: #2a2a10; }}"
    f"QPushButton:checked {{"
    f"  background: #2a2800; color: #ffcc00; border-color: #ffcc00;"
    f"}}"
)

# 작은 보조 버튼 (…, apply 등)
BTN_SMALL = (
    f"QPushButton {{"
    f"  background: {C_BG_MED}; color: {C_ACCENT};"
    f"  border: 1px solid {C_BORDER_HI}; border-radius: 4px;"
    f"  font-family: '{Fonts.MONO}'; font-weight: bold;"
    f"  font-size: {Sizes.CTRL}; padding: 4px 8px;"
    f"}}"
    f"QPushButton:hover {{ background: #1a3a60; }}"
    f"QPushButton:disabled {{ color: #1a2840; background: {C_BG_DEEP}; }}"
)


# ─────────────────────────────────────────────────────────────────────────────
# 입력 위젯 QSS
# ─────────────────────────────────────────────────────────────────────────────

SPIN_STYLE = (
    f"QDoubleSpinBox, QSpinBox {{"
    f"  background: {C_BG_DEEP}; border: 1px solid {C_BORDER};"
    f"  color: {C_TEXT}; border-radius: 3px;"
    f"  font-family: '{Fonts.MONO}'; font-size: {Sizes.CTRL};"
    f"  padding: 3px 5px; min-height: 22px;"
    f"}}"
)

COMBO_STYLE = (
    f"QComboBox {{"
    f"  background: {C_BG_DEEP}; border: 1px solid {C_BORDER};"
    f"  color: {C_TEXT}; border-radius: 3px;"
    f"  font-family: '{Fonts.MONO}'; font-size: {Sizes.CTRL};"
    f"  padding: 3px 5px; min-height: 22px;"
    f"}}"
    f"QComboBox::drop-down {{ border: none; }}"
    f"QComboBox QAbstractItemView {{"
    f"  background: {C_BG_MED}; color: {C_TEXT};"
    f"  selection-background-color: {C_BORDER};"
    f"}}"
)

EDIT_STYLE = (
    f"QLineEdit {{"
    f"  background: {C_BG_DEEP}; border: 1px solid {C_BORDER};"
    f"  color: {C_TEXT}; border-radius: 3px;"
    f"  font-family: '{Fonts.MONO}'; font-size: {Sizes.CTRL};"
    f"  padding: 3px 5px; min-height: 22px;"
    f"}}"
)

CHECKBOX_STYLE = (
    f"QCheckBox {{"
    f"  color: {C_TEXT_DIM}; font-family: '{Fonts.UI}'; font-size: {Sizes.CTRL};"
    f"}}"
    f"QCheckBox::indicator {{"
    f"  width: 13px; height: 13px;"
    f"  border: 1px solid {C_BORDER}; border-radius: 2px;"
    f"  background: {C_BG_DEEP};"
    f"}}"
    f"QCheckBox::indicator:checked {{"
    f"  background: {C_ACCENT}; border-color: {C_ACCENT};"
    f"}}"
)

SLIDER_STYLE = (
    f"QSlider::groove:horizontal {{"
    f"  height: 4px; background: {C_BORDER}; border-radius: 2px;"
    f"}}"
    f"QSlider::handle:horizontal {{"
    f"  background: {C_ACCENT}; border: 1px solid {C_ACCENT};"
    f"  width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;"
    f"}}"
    f"QSlider::sub-page:horizontal {{"
    f"  background: {C_ACCENT}; border-radius: 2px;"
    f"}}"
)


# ─────────────────────────────────────────────────────────────────────────────
# 컨테이너 QSS
# ─────────────────────────────────────────────────────────────────────────────

PROGRESS_STYLE = (
    f"QProgressBar {{"
    f"  background: {C_BG_DEEP}; border: 1px solid {C_BORDER};"
    f"  border-radius: 3px; color: {C_ACCENT};"
    f"  font-family: '{Fonts.MONO}'; font-size: {Sizes.CTRL};"
    f"  text-align: center;"
    f"}}"
    f"QProgressBar::chunk {{ background: {C_DANGER}; border-radius: 2px; }}"
)

PROGRESS_ACCENT_STYLE = (
    f"QProgressBar {{"
    f"  background: {C_BG_DEEP}; border: 1px solid {C_BORDER};"
    f"  border-radius: 3px; color: {C_ACCENT};"
    f"  font-family: '{Fonts.MONO}'; font-size: {Sizes.CTRL};"
    f"  text-align: center;"
    f"}}"
    f"QProgressBar::chunk {{ background: {C_ACCENT}; border-radius: 2px; }}"
)

TEXTEDIT_LOG = (
    f"QTextEdit {{"
    f"  background: {C_BG_DEEP}; border: 1px solid {C_BORDER};"
    f"  color: {C_TEXT_LOG}; font-family: '{Fonts.MONO}'; font-size: {Sizes.LOG};"
    f"}}"
)

TABLE_STYLE = (
    f"QTableWidget {{"
    f"  background: {C_BG_DEEP}; gridline-color: {C_BORDER};"
    f"  color: {C_TEXT}; font-family: '{Fonts.MONO}'; font-size: {Sizes.LOG};"
    f"  border: none;"
    f"}}"
    f"QHeaderView::section {{"
    f"  background: {C_BG_MED}; color: {C_ACCENT};"
    f"  border: 1px solid {C_BORDER}; font-family: '{Fonts.UI}';"
    f"  font-size: {Sizes.CTRL}; font-weight: bold; padding: 4px 2px;"
    f"}}"
    f"QTableWidget::item:selected {{ background: {C_BORDER}; }}"
)

LIST_STYLE = (
    f"QListWidget {{"
    f"  background: {C_BG_DEEP}; border: 1px solid {C_BORDER}; color: {C_TEXT};"
    f"}}"
    f"QListWidget::item {{ padding: 2px; border: 1px solid #0f2040; }}"
    f"QListWidget::item:selected {{ background: {C_BORDER_HI}; border: 1px solid {C_ACCENT}; }}"
)

SCROLL_AREA_STYLE = (
    f"QScrollArea {{ border: none; background: {C_BG_DARK}; }}"
)

SPLITTER_H_STYLE = (
    f"QSplitter::handle:horizontal {{"
    f"  background: {C_ACCENT}; width: 4px;"
    f"}}"
)

SPLITTER_V_STYLE = (
    f"QSplitter::handle:vertical {{"
    f"  background: {C_BORDER}; height: 4px; margin: 1px 0;"
    f"}}"
    f"QSplitter::handle:vertical:hover {{ background: {C_ACCENT}; }}"
)


# ─────────────────────────────────────────────────────────────────────────────
# GroupBox 스타일 팩토리
# ─────────────────────────────────────────────────────────────────────────────

def grp_style(color: str = C_DANGER) -> str:
    """
    QGroupBox.setStyleSheet()에 직접 전달.

    기본(빨강): box.setStyleSheet(grp_style())
    청록:       box.setStyleSheet(grp_style(C_ACCENT))
    노랑:       box.setStyleSheet(grp_style(C_WARN))
    """
    return (
        f"QGroupBox {{"
        f"  border: 1px solid {C_BORDER}; border-radius: 6px;"
        f"  margin-top: 10px; font-family: '{Fonts.MONO}';"
        f"  font-size: {Sizes.CTRL}; color: {color};"
        f"  letter-spacing: 2px; font-weight: bold;"
        f"}}"
        f"QGroupBox::title {{"
        f"  subcontrol-origin: margin; left: 10px; padding: 0 4px;"
        f"}}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 인라인 레이블 스타일 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def lbl(
    color: str = C_TEXT_DIM,
    size: str = Sizes.CTRL,
    mono: bool = False,
    bold: bool = False,
) -> str:
    """
    QLabel.setStyleSheet(lbl(...)) 용 인라인 스타일 문자열 반환.

    lbl()                          → 기본 흐린 레이블
    lbl(C_ACCENT)                  → 청록 텍스트
    lbl(C_TEXT, mono=True)         → 모노스페이스 수치
    lbl(C_DANGER, bold=True)       → 빨강 굵게 (에러 메시지 등)
    """
    font = Fonts.MONO if mono else Fonts.UI
    weight = "bold;" if bold else ""
    return f"color: {color}; font-family: '{font}'; font-size: {size}; {weight}"


# ─────────────────────────────────────────────────────────────────────────────
# 로그 HTML 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def log_color(msg: str) -> str:
    """메시지 내용에 따라 적절한 로그 색상 반환."""
    if any(k in msg for k in ("✅", "▶", "💾", "📸", "📂")):
        return C_ACCENT
    if any(k in msg for k in ("⚠️",)):
        return C_WARN
    if any(k in msg for k in ("❌", "FAIL", "실패", "오류", "Error")):
        return C_DANGER
    if any(k in msg for k in ("⏱", "ETA", "예상")):
        return C_WARN
    if any(k in msg for k in ("■", "해제", "정지")):
        return C_TEXT_DEAD
    return C_TEXT_LOG


def log_html(msg: str, ts: str) -> str:
    """
    QTextEdit.append()에 전달할 HTML 행 반환.

    append(log_html("✅ 완료", "12:34:56"))
    """
    color = log_color(msg)
    ts_span = (
        f"<span style='color:{C_TEXT_DEAD};"
        f"font-size:{Sizes.SMALL}'>[{ts}]</span>"
    )
    msg_span = f"<span style='color:{color}'>{msg}</span>"
    return f"{ts_span} {msg_span}"
