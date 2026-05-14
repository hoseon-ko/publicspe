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

# Windows COM Threading Mode Fix (RPC_E_CHANGED_MODE 0x80010106)
if sys.platform == 'win32':
    # Qt가 자체적으로 OLE를 초기화하지 않도록 설정 (충돌 방지)
    os.environ["QT_COM_INIT"] = "0"
    if not hasattr(sys, 'coinit_flags'):
        sys.coinit_flags = 2  # MTA (Multi-Threaded Apartment)

import signal
import threading
import traceback
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QTimer, Qt, QMetaObject

from theme.dark_theme import DARK_THEME_QSS
from ui.main_window import MainWindow
from core.spe_reader import SpeFile
from core.logger import app_logger, sys_logger

_FATAL_ERROR_SHOWN = False


def _show_fatal_dialog(err_msg: str):
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle("Fatal Error")
    msg_box.setText("치명적인 오류가 발생했습니다.\n아래 상세 정보를 확인해 주세요.")
    msg_box.setInformativeText("확인을 누르면 프로그램이 종료됩니다.")
    msg_box.setDetailedText(err_msg)
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg_box.exec()


def _global_exception_handler(exc_type, exc_value, exc_traceback):
    global _FATAL_ERROR_SHOWN
    if issubclass(exc_type, KeyboardInterrupt) or _FATAL_ERROR_SHOWN:
        return

    _FATAL_ERROR_SHOWN = True
    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    sys_logger.critical(f"FATAL Uncaught Exception:\n{err_msg}")

    app = QApplication.instance()
    if app:
        # 메시지 박스를 먼저 보여준 뒤 종료한다.
        try:
            _show_fatal_dialog(err_msg)
        except Exception:
            pass
        return

    return


def _thread_exception_handler(args: threading.ExceptHookArgs):
    _global_exception_handler(args.exc_type, args.exc_value, args.exc_traceback)


sys.excepthook = _global_exception_handler
threading.excepthook = _thread_exception_handler


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
    # signal 핸들러는 별도 스레드에서 실행될 수 있으므로, 
    # 직접 UI를 건드리지 않고 플래그만 세우거나 가벼운 quit 요청만 합니다.
    def _sigint_handler(*_):
        # app.quit()은 Thread-Safe 하므로 안전하게 호출 가능
        QMetaObject.invokeMethod(app, "quit", Qt.ConnectionType.QueuedConnection)

    signal.signal(signal.SIGINT, _sigint_handler)

    # Python signal 체크가 Qt 이벤트 루프에 묻히지 않도록 200ms 주기 틱
    _sig_timer = QTimer()
    _sig_timer.setInterval(200)
    _sig_timer.timeout.connect(lambda: None)   # Python 인터프리터 깨우기용
    _sig_timer.start()

    # aboutToQuit: 정상 종료 / QApplication.quit() 모두 커버
    app.aboutToQuit.connect(window.save_all_settings)

    exit_code = app.exec()
    
    # ── 종료 시 리소스 명시적 해제 (Thread-Safe Shutdown) ─────────────────
    window.hide()
    window.deleteLater()
    app.processEvents() # deleteLater 처리를 위한 이벤트 루프 한 번 더 실행
    
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