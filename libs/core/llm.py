import httpx
from dataclasses import dataclass
from typing import Any, Optional, List
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.embeddings import Embeddings
from libs.core.settings import settings


@dataclass(frozen=True)
class LLMMeta:
    """런타임 LLM 라벨 (트레이스/LLM_LOG 공통 라벨용).

    ``served_by`` 는 ``*_BASE_URL`` 이 비어있으면 ``openai`` (hosted), 아니면 ``vllm``.
    ``tool_call_parser`` 는 vLLM 일 때만 채워지며, 운영자가 env 로 선언한 라벨을
    그대로 가져온다 (검증/주입 X).
    """
    model_id: str
    served_by: str            # "openai" | "vllm"
    tool_call_parser: Optional[str]


def get_llm_meta(model_type: str) -> LLMMeta:
    """Return :class:`LLMMeta` for ``"chat"`` or ``"reasoning"``."""
    if model_type == "chat":
        base_url = settings.CHAT_LLM_BASE_URL
        model_id = settings.CHAT_LLM_MODEL
        parser = settings.CHAT_LLM_TOOL_PARSER or None
    elif model_type == "reasoning":
        base_url = settings.REASONING_LLM_BASE_URL
        model_id = settings.REASONING_LLM_MODEL
        parser = settings.REASONING_LLM_TOOL_PARSER or None
    else:
        raise ValueError(f"unknown model_type: {model_type!r}")
    served_by = "vllm" if base_url else "openai"
    return LLMMeta(
        model_id=model_id,
        served_by=served_by,
        tool_call_parser=parser if served_by == "vllm" else None,
    )


def extract_reasoning(ai_message: Any) -> Optional[str]:
    """Extract ``reasoning_content`` if the model exposed it (gpt-oss / o-series).

    Qwen3 류는 본문 인라인 (`<think>...</think>`) 이라 분리하지 않는다 — 본 함수는
    ``additional_kwargs.reasoning_content`` 만 본다.
    """
    ak = getattr(ai_message, "additional_kwargs", None) or {}
    if not isinstance(ak, dict):
        return None
    rc = ak.get("reasoning_content")
    if isinstance(rc, str) and rc.strip():
        return rc
    return None


class CustomProductionEmbeddings(Embeddings):
    """프로덕션 전용 커스텀 API 임베딩 클래스"""
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        with httpx.Client() as client:
            # 프로덕션 커스텀 API 규격: {"texts": [...]}
            response = client.post(self.base_url, json={"texts": texts}, timeout=60.0)
            response.raise_for_status()
            return response.json()["embeddings"]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(self.base_url, json={"texts": texts}, timeout=60.0)
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
