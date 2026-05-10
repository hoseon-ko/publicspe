"""
main.py
통합 실험실 제어 앱 진입점.

탭 구성:
  Tab 1 — Live Control  : 실시간 카메라 (HIKVISION/Picam) + Picomotor 8742
  Tab 2 — Acquisition   : Picam 배치 획득 + SPE 3.0 저장
  Tab 3 — SPE Analysis  : SpeAnalyze 전체 기능 (ROI / Profile / Histogram)
"""

import os
import sys
import signal
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QTimer

from theme.dark_theme import DARK_THEME_QSS
#from ui.histogram_range_widget import HistogramRangeWidget
from ui.main_window import MainWindow
from core.spe_reader import SpeFile
from core.logger import app_logger, sys_logger

def _global_exception_handler(exc_type, exc_value, exc_traceback):
    """[Phase 1] 전역 예외 처리기: 미처리 에러를 가로채어 로그 파일에 기록"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
        
    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    sys_logger.critical(f"Uncaught Exception:\n{err_msg}")
    
    if QApplication.instance():
      
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Fatal Error")
        msg_box.setText("프로그램 실행 중 치명적인 오류가 발생했습니다.\n로그 파일(logs/)을 확인해주세요.")
        msg_box.setDetailedText(err_msg)
        msg_box.exec()

sys.excepthook = _global_exception_handler


def main():
    # DirectWrite 폰트 관련 무해한 경고 로그 숨기기
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts=false"
    
    from core.logger import clear_ui_callbacks
    clear_ui_callbacks()
    sys_logger.info("SpeAnalyze Application Started.")
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

    exit_code = app.exec()
    app_logger.info(f"SpeAnalyze Application Exited with code {exit_code}.")
    sys.exit(exit_code)


if __name__ == "__main__":
   main()

# if __name__ == "__main__":
#     import sys
#     from PySide6.QtWidgets import QApplication
#     app = QApplication(sys.argv)
#     window = HistogramRangeWidget() # 본인의 클래스 명칭으로 변경
#     window.show()
#     sys.exit(app.exec())