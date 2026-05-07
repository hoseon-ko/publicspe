"""
main.py
통합 실험실 제어 앱 진입점.

탭 구성:
  Tab 1 — Live Control  : 실시간 카메라 (HIKVISION/Picam) + Picomotor 8742
  Tab 2 — Acquisition   : Picam 배치 획득 + SPE 3.0 저장
  Tab 3 — SPE Analysis  : SpeAnalyze 전체 기능 (ROI / Profile / Histogram)
"""

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

from theme.dark_theme import DARK_THEME_QSS
#from ui.histogram_range_widget import HistogramRangeWidget
from ui.main_window import MainWindow
from core.spe_reader import SpeFile


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SpeAnalyze — Integrated Lab Control")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_THEME_QSS)

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = MainWindow(spe_class=SpeFile)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
   main()

# if __name__ == "__main__":
#     import sys
#     from PySide6.QtWidgets import QApplication
#     app = QApplication(sys.argv)
#     window = HistogramRangeWidget() # 본인의 클래스 명칭으로 변경
#     window.show()
#     sys.exit(app.exec())