import os
import logging
import requests
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))

def setup_logger(name: str, log_file_name: str, level=logging.INFO):
    """로거 설정 및 반환"""
    log_path = Path("log")
    log_path.mkdir(parents=True, exist_ok=True)
    
    # 날짜별 로그 파일명 생성
    file_path = log_path / f"{log_file_name}_{datetime.today().strftime('%Y%m%d')}.log"

    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 중복 핸들러 추가 방지
    if not logger.handlers:
        file_handler = RotatingFileHandler(file_path, maxBytes=100 * 1024 * 1024, backupCount=5, encoding="utf-8")
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 콘솔 출력도 원하면 추가 (선택사항)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger

def send_discord_message(text: str, webhook_url: str = None, env_var_name: str = "DISCORD_SCT_URL"):
    """
    디스코드 채널로 메시지 전송
    webhook_url이 직접 주어지면 그걸 쓰고, 없으면 env_var_name 환경변수에서 읽어옴.
    """
    if not webhook_url:
        webhook_url = os.getenv(env_var_name)

    if not webhook_url:
        print(f"Warning: Discord webhook URL not found (env: {env_var_name})")
        return

    # KST 시간 포함
    timestamp = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    payload = {"content": f"[{timestamp}] {text}"}

    try:
        requests.post(webhook_url, json=payload, timeout=5)
    except requests.RequestException as e:
        print(f"Discord message send failed: {e}")
