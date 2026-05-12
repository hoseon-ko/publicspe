"""
core/logger.py
프로젝트 전역 파일 기반 로깅 시스템.
콘솔 출력(개발용) 및 파일 출력(운영용)을 동시에 지원하며,
에러 발생 시 시간대별로 정확한 원인을 추적할 수 있도록 돕는다.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Callable, List, Optional

# UI와 연결하기 위한 콜백 리스트
_ui_callbacks: List[Callable[..., None]] = []
_logging_lock = False # 재진입 방지용 플래그

class UIBridgeHandler(logging.Handler):
    """로깅 메시지를 등록된 UI 콜백으로 전달하는 핸들러."""
    def emit(self, record):
        global _logging_lock
        if _logging_lock:
            return
            
        try:
            _logging_lock = True
            msg = self.format(record)
            # logger name에서 카테고리 추출 (예: 'SpeAnalyze.dev' -> 'dev')
            parts = record.name.split('.')
            category = parts[-1] if len(parts) > 1 else "sys"
            if category not in ["sys", "dev", "cam", "calc"]:
                category = "sys"

            dead_callbacks = []
            for cb in list(_ui_callbacks):
                try:
                    try:
                        cb(msg, category, int(record.levelno))
                    except TypeError:
                        # 기존 2-인자 콜백과 하위 호환
                        cb(msg, category)
                except Exception:
                    # Qt 위젯 소멸 후 남은 콜백은 자동 정리
                    dead_callbacks.append(cb)

            for cb in dead_callbacks:
                try:
                    _ui_callbacks.remove(cb)
                except ValueError:
                    pass
        finally:
            _logging_lock = False

def register_ui_callback(callback: Callable[..., None]):
    """UI에서 로그 메시지를 수신할 콜백 등록.

    콜백 시그니처:
    - 신규: (msg, category, levelno)
    - 하위호환: (msg, category)
    """
    if callback not in _ui_callbacks:
        _ui_callbacks.append(callback)

def clear_ui_callbacks():
    """모든 UI 콜백 초기화 (재시작 시 중복 방지용)."""
    _ui_callbacks.clear()

def setup_logger() -> logging.Logger:
    root_logger = logging.getLogger()
    
    if root_logger.hasHandlers():
        return logging.getLogger("SpeAnalyze")
        
    root_logger.setLevel(logging.DEBUG)
    
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    
    # 공통 포맷
    file_formatter = logging.Formatter('[%(asctime)s] %(levelname)-8s [%(name)s:%(lineno)d] %(message)s')
    console_formatter = logging.Formatter('%(levelname)-8s | %(name)-20s | %(message)s')
    
    # 1. 통합 로그 (모든 로그 저장)
    main_file = os.path.join(log_dir, f"spe_analyze_{today}.log")
    main_handler = RotatingFileHandler(main_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    main_handler.setLevel(logging.INFO)
    main_handler.setFormatter(file_formatter)
    
    # 2. 하드웨어 전용 로그 (디버깅 용이성)
    hw_file = os.path.join(log_dir, f"hardware_{today}.log")
    hw_handler = RotatingFileHandler(hw_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    hw_handler.setLevel(logging.DEBUG)
    hw_handler.setFormatter(file_formatter)
    
    class HWFilter(logging.Filter):
        def filter(self, record):
            return "motor" in record.name or "camera" in record.name
    hw_handler.addFilter(HWFilter())
    
    # 3. 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(console_formatter)
    
    root_logger.addHandler(main_handler)
    root_logger.addHandler(hw_handler)
    root_logger.addHandler(console_handler)
    
    # 4. UI 브릿지 핸들러
    ui_handler = UIBridgeHandler()
    ui_handler.setLevel(logging.DEBUG)
    ui_handler.setFormatter(logging.Formatter('%(message)s'))  # UI 포맷은 자체 시간 표시 사용
    root_logger.addHandler(ui_handler)
    
    # 서드파티 라이브러리 노이즈 제거
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PyQt6").setLevel(logging.WARNING)
    
    return logging.getLogger("SpeAnalyze")

# 전역 카테고리별 로거 인스턴스
app_logger = setup_logger()
sys_logger  = logging.getLogger("SpeAnalyze.sys")
dev_logger  = logging.getLogger("SpeAnalyze.dev")
cam_logger  = logging.getLogger("SpeAnalyze.cam")
calc_logger = logging.getLogger("SpeAnalyze.calc")

