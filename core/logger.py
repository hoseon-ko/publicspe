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
    
    # 서드파티 라이브러리 노이즈 제거
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PyQt6").setLevel(logging.WARNING)
    
    return logging.getLogger("SpeAnalyze")

# 전역 기본 로거 인스턴스
app_logger = setup_logger()

