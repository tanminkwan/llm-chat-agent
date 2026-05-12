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
    LOG_LEVEL: str = "INFO"
    MEMORY_MAX_MESSAGES: int = 10

    # Observability
    # 메뉴의 Grafana 링크 대상. Grafana 컨테이너의 GF_SERVER_ROOT_URL 와 동일한 값이라 재사용한다.
    GRAFANA_ROOT_URL: Optional[str] = None

    # ------------------------------------------------------------------
    # Phase 07 — Tool Lab
    # ------------------------------------------------------------------

    # 메뉴/엔드포인트 전체 on/off. False 면 라우터 자체를 등록하지 않는다.
    TOOLLAB_ENABLED: bool = True
    # 콤마 구분 OIDC 그룹. 동적 코드 등록 가능 그룹.
    # Admin 은 항상 허용 (group 매칭과 별도). system 도구 편집·삭제는 Admin 전용으로
    # routers/toollab._load_owned 에서 별도 차단됨.
    TOOLLAB_ALLOWED_GROUPS: str = "Admin,User"
    # 단일 handler 호출 wall-clock 타임아웃 (ms).
    TOOLLAB_HANDLER_TIMEOUT_MS: int = 2000
    # 단일 run 의 LLM↔tool 루프 turn 상한 (request 별 override 가능).
    TOOLLAB_MAX_TOOL_ITERATIONS: int = 8
    # request 가 override 해도 넘지 못하는 hard cap.
    TOOLLAB_MAX_TOOL_ITERATIONS_HARD: int = 16
    # 한 run 에 노출 가능한 도구 수 상한 (스키마 비대화 방지).
    TOOLLAB_MAX_TOOLS_PER_RUN: int = 32
    # 핸들러 동시 실행용 스레드풀 크기.
    TOOLLAB_HANDLER_THREADPOOL_SIZE: int = 4

    # vLLM 백엔드일 때 그 인스턴스가 실제 사용 중인 --tool-call-parser 값을 그대로
    # 적는다 (라벨링 전용 — 검증·주입 X). hosted OpenAI 면 빈 문자열.
    # §RQ §4.3.1 서빙 매트릭스 참조.
    CHAT_LLM_TOOL_PARSER: str = ""
    REASONING_LLM_TOOL_PARSER: str = ""

settings = Settings()
