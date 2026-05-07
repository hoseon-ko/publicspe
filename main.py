"""
main.py
통합 실험실 제어 앱 진입점.

탭 구성:
  Tab 1 — Live Control  : 실시간 카메라 (HIKVISION/Picam) + Picomotor 8742
  Tab 2 — Acquisition   : Picam 배치 획득 + SPE 3.0 저장
  Tab 3 — SPE Analysis  : SpeAnalyze 전체 기능 (ROI / Profile / Histogram)
"""

import sys
import signal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QTimer

from theme.dark_theme import DARK_THEME_QSS
#from ui.histogram_range_widget import HistogramRangeWidget
from ui.main_window import MainWindow
from core.spe_reader import SpeFile


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("SpeAnalyze")
    app.setApplicationName("SpeAnalyze")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_THEME_QSS)

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = MainWindow(spe_class=SpeFile)
    window.show()

    # ── Ctrl+C (SIGINT) 처리 ─────────────────────────────────────────
    # Python SIGINT가 Qt 이벤트 루프를 우회하므로, 주기적으로 Python 체크포인트를
    # 강제 실행해서 KeyboardInterrupt → window.close() 흐름으로 연결
    def _sigint_handler(*_):
        window.close()
        app.quit()

    signal.signal(signal.SIGINT, _sigint_handler)

    # Python signal 체크가 Qt 이벤트 루프에 묻히지 않도록 200ms 주기 틱
    _sig_timer = QTimer()
    _sig_timer.setInterval(200)
    _sig_timer.timeout.connect(lambda: None)   # Python 인터프리터 깨우기용
    _sig_timer.start()

    # aboutToQuit: 정상 종료 / QApplication.quit() 모두 커버
    app.aboutToQuit.connect(window.save_all_settings)

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