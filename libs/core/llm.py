from typing import Optional
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from libs.core.settings import settings

class LLMGateway:
    """LLM 및 Embedding 모델을 생성하는 게이트웨이 클래스"""

    @staticmethod
    def get_chat_llm(streaming: bool = True, temperature: float = 0.7) -> ChatOpenAI:
        """기본 대화형 LLM 반환"""
        kwargs = {
            "model": settings.CHAT_LLM_MODEL,
            "api_key": settings.CHAT_LLM_API_KEY,
            "streaming": streaming,
        }
        if settings.CHAT_LLM_BASE_URL:
            kwargs["base_url"] = settings.CHAT_LLM_BASE_URL
        if settings.CHAT_LLM_USE_TEMPERATURE:
            kwargs["temperature"] = temperature
            
        return ChatOpenAI(**kwargs)

    @staticmethod
    def get_reasoning_llm(streaming: bool = True, temperature: Optional[float] = None) -> ChatOpenAI:
        """추론 전용 LLM 반환 (o1/o4-mini 등)"""
        kwargs = {
            "model": settings.REASONING_LLM_MODEL,
            "api_key": settings.REASONING_LLM_API_KEY,
            "streaming": streaming,
        }
        if settings.REASONING_LLM_BASE_URL:
            kwargs["base_url"] = settings.REASONING_LLM_BASE_URL
        # 설정에서 허용된 경우에만 temperature 적용
        if settings.REASONING_LLM_USE_TEMPERATURE and temperature is not None:
            kwargs["temperature"] = temperature
            
        return ChatOpenAI(**kwargs)

    @staticmethod
    def get_embeddings() -> OpenAIEmbeddings:
        """텍스트 임베딩 모델 반환 (설정된 차원 적용)"""
        kwargs = {
            "model": settings.EMBEDDING_MODEL,
            "api_key": settings.EMBEDDING_API_KEY,
            "dimensions": settings.EMBEDDING_DIM
        }
        if settings.EMBEDDING_BASE_URL:
            kwargs["base_url"] = settings.EMBEDDING_BASE_URL
            
        return OpenAIEmbeddings(**kwargs)
