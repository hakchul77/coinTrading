from pydantic import Field
from pydantic_settings import BaseSettings
from typing import List, get_type_hints
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "한국투자증권 Open API 매매"
    PROJECT_DESCRIPTION: str = "국내주식 Split 매매"
    PROJECT_VERSION: str = "1.0.0"

    # DEBUG 설정 추가
    DEBUG: bool = Field(default=True, description="디버그 모드 활성화 여부")
    OWNER: str = Field(default="KKS", description="계좌사용자")

    CORS_ORIGINS: List[str] = ["*"]

    SUPABASE_URL: str = Field(..., description="SUPABASE URL")
    SUPABASE_KEY: str = Field(..., description="SUPABASE API KEY")

    DISCORD_URL: str = Field(..., description="디스코드 알림 URL")
    DISCORD_COIN_URL: str = Field(..., description="디스코드 코인 알림 URL")

    # 한국투자증권 API 설정
    KIS_USE_MOCK: bool = Field(default=True, description="모의투자 사용 여부")

    KIS_MOCK_URL: str = Field(
        default="https://openapivts.koreainvestment.com:29443",
        description="한국투자증권 API 기본 URL (모의투자용)"
    )
    KIS_MOCK_APPKEY: str = Field(..., description="한국투자증권 API 앱키")
    KIS_MOCK_APPSECRET: str = Field(..., description="한국투자증권 API 앱시크릿")
    KIS_MOCK_CANO: str = Field(..., description="계좌번호 앞 8자리")

    KIS_REAL_URL: str = Field(
        default="https://openapi.koreainvestment.com:9443",
        description="한국투자증권 API 기본 URL (실제투자용)"
    )
    KIS_REAL_APPKEY: str = Field(..., description="한국투자증권 API 앱키")
    KIS_REAL_APPSECRET: str = Field(..., description="한국투자증권 API 앱시크릿")
    KIS_REAL_CANO: str = Field(..., description="계좌번호 앞 8자리")

    KIS_KKS_REAL_APPKEY: str = Field(..., description="한국투자증권 API 앱키")
    KIS_KKS_REAL_APPSECRET: str = Field(..., description="한국투자증권 API 앱시크릿")
    KIS_KKS_REAL_CANO: str = Field(..., description="계좌번호 앞 8자리")

    KIS_ACNT_PRDT_CD: str = Field(..., description="계좌번호 뒤 2자리")
    TR_ID: str = os.getenv("TR_ID")

    @property
    def kis_base_url(self) -> str:
        """사용할 한국투자증권 API URL 반환"""
        return self.KIS_MOCK_URL if self.KIS_USE_MOCK else self.KIS_REAL_URL

    @property
    def kis_appkey(self) -> str:
        """사용할 한국투자증권 APPKEY 반환"""
        if self.OWNER == "KKS":
            return self.KIS_MOCK_APPKEY if self.KIS_USE_MOCK else self.KIS_KKS_REAL_APPKEY
        return self.KIS_MOCK_APPKEY if self.KIS_USE_MOCK else self.KIS_REAL_APPKEY

    @property
    def kis_appsecret(self) -> str:
        """사용할 한국투자증권 APPKEY 반환"""
        if self.OWNER == "KKS":
            return self.KIS_MOCK_APPSECRET if self.KIS_USE_MOCK else self.KIS_KKS_REAL_APPSECRET
        return self.KIS_MOCK_APPSECRET if self.KIS_USE_MOCK else self.KIS_REAL_APPSECRET

    @property
    def kis_cano(self) -> str:
        """사용할 한국투자증권 APPKEY 반환"""
        if self.OWNER == "KKS":
            return self.KIS_MOCK_CANO if self.KIS_USE_MOCK else self.KIS_KKS_REAL_CANO
        return self.KIS_MOCK_CANO if self.KIS_USE_MOCK else self.KIS_REAL_CANO

    class Config:
        env_file = "../../.env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# 싱글톤 설정 객체 생성
settings = Settings()