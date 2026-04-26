from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # OIDC Settings
    OIDC_ISSUER: str = "https://idp.mwm.local:20443"
    OIDC_CLIENT_ID: str
    OIDC_CLIENT_SECRET: str
    OIDC_REDIRECT_URI: str = "https://llm-agent.mwm.local:21443/auth/callback"

    # LLM Settings
    CHAT_LLM_BASE_URL: Optional[str] = None
    CHAT_LLM_MODEL: str = "gpt-4o"
    CHAT_LLM_LABEL: str = "일반 대화"
    CHAT_LLM_API_KEY: str
    CHAT_LLM_USE_TEMPERATURE: bool = True

    REASONING_LLM_BASE_URL: Optional[str] = None
    REASONING_LLM_MODEL: str = "o4-mini"
    REASONING_LLM_LABEL: str = "심층 추론"
    REASONING_LLM_API_KEY: str
    REASONING_LLM_USE_TEMPERATURE: bool = False

    EMBEDDING_BASE_URL: Optional[str] = None
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_API_KEY: Optional[str] = None
    EMBEDDING_DIM: int = 1536
    EMBEDDING_USE_CUSTOM: bool = False

    # Qdrant Settings
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: Optional[str] = None

    # Database Settings
    DATABASE_URL: str = "postgresql+asyncpg://admin:password@db:5432/llm_agent"

    # App Settings
    APP_NAME: str = "llm-chat-agent"
    DEBUG: bool = False
    MEMORY_MAX_MESSAGES: int = 10

settings = Settings()
