import httpx
from typing import Optional, List
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.embeddings import Embeddings
from libs.core.settings import settings


class CustomProductionEmbeddings(Embeddings):
    """프로덕션 전용 커스텀 API 임베딩 클래스"""
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        with httpx.Client() as client:
            # 프로덕션 커스텀 API 규격: {"tests": [...]}
            response = client.post(self.base_url, json={"tests": texts}, timeout=60.0)
            response.raise_for_status()
            return response.json()["embeddings"]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(self.base_url, json={"tests": texts}, timeout=60.0)
            response.raise_for_status()
            return response.json()["embeddings"]

    async def aembed_query(self, text: str) -> List[float]:
        embeddings = await self.aembed_documents([text])
        return embeddings[0]

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
    def get_embeddings() -> Embeddings:
        """텍스트 임베딩 모델 반환 (설정 플래그에 따라 커스텀 방식 사용 가능)"""
        if settings.EMBEDDING_USE_CUSTOM:
            return CustomProductionEmbeddings(
                base_url=settings.EMBEDDING_BASE_URL,
                model=settings.EMBEDDING_MODEL
            )
            
        kwargs = {
            "model": settings.EMBEDDING_MODEL,
            "api_key": settings.EMBEDDING_API_KEY,
            "dimensions": settings.EMBEDDING_DIM
        }
        if settings.EMBEDDING_BASE_URL:
            kwargs["base_url"] = settings.EMBEDDING_BASE_URL
            
        return OpenAIEmbeddings(**kwargs)
