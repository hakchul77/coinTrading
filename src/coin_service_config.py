from pydantic import Field
from pydantic_settings import BaseSettings
from typing import List, Optional
import os
import json
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "Coin & Stock Trading Bot"
    PROJECT_DESCRIPTION: str = "Integrated Trading Bot for Bithumb/Upbit & KIS"
    PROJECT_VERSION: str = "2.0.0"

    # DEBUG 설정 추가
    DEBUG: bool = Field(default=True, description="디버그 모드 활성화 여부")
    OWNER: str = Field(default="KKS", description="계좌사용자")

    CORS_ORIGINS: List[str] = ["*"]

    # -----------------------------------------------------------
    # [Common] External Services
    # -----------------------------------------------------------
    SUPABASE_URL: Optional[str] = Field(None, description="SUPABASE URL")
    SUPABASE_KEY: Optional[str] = Field(None, description="SUPABASE API KEY")

    DISCORD_URL: Optional[str] = Field(None, description="디스코드 알림 URL (General/Stock)")
    DISCORD_COIN_URL: Optional[str] = Field(None, description="디스코드 알림 URL (Coin)")
    
    # Legacy env var mapping
    DISCORD_SCT_URL: Optional[str] = Field(None, description="디스코드 알림 (Coin Main) - Legacy")
    DISCORD_DCT_URL: Optional[str] = Field(None, description="디스코드 알림 (Adjust Trading) - Legacy")

    class Config:
        env_file = "../../.env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore" # 정의되지 않은 필드는 무시

# 싱글톤 설정 객체 생성
settings = Settings()