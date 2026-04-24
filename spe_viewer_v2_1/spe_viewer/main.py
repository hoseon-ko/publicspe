"""
main.py
SPE Viewer 진입점

사용법:
    from your_spe_module import SpeClass   # 기존 SPE 클래스 import
    # main.py 하단 spe_class= 부분에 넣어주세요
"""

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

from theme.dark_theme import DARK_THEME_QSS
from ui.main_window import MainWindow

# ── 여기에 기존 SPE 클래스를 import 하세요 ──────────────
# from your_spe_module import SpeClass
# ────────────────────────────────────────────────────────


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SPE Viewer")
    app.setStyle("Fusion")           # Fusion 스타일 베이스 (QSS와 잘 맞음)
    app.setStyleSheet(DARK_THEME_QSS)

    # 기본 폰트
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # spe_class 자리에 실제 클래스 넣기
    # window = MainWindow(spe_class=SpeClass)
    window = MainWindow(spe_class=None)   # 테스트용: None이면 경고창 표시
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
