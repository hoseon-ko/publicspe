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

def get_logger(name: str = "SpeAnalyze") -> logging.Logger:
    logger = logging.getLogger(name)
    
    # 이미 핸들러가 추가된 경우 중복 추가 방지
    if logger.hasHandlers():
        return logger
        
    logger.setLevel(logging.DEBUG)
    
    # 1. 파일 핸들러 (logs/ 폴더에 자동 저장, 파일당 최대 5MB, 3개 백업)
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    log_file = os.path.join(log_dir, f"spe_analyze_{today}.log")
    
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    
    # 2. 콘솔 핸들러 (기존 print문 대체)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_formatter = logging.Formatter(
        '%(levelname)-8s | %(name)s | %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# 전역 기본 로거 인스턴스
app_logger = get_logger()
