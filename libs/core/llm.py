from typing import Optional
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from libs.core.settings import settings

class LLMGateway:
    """
    LLM Gateway abstraction layer as per CLAUDE.md requirements.
    Supports both OpenAI and vLLM (OpenAI-compatible).
    """
    
    @staticmethod
    def get_chat_llm(temperature: float = 0.7) -> ChatOpenAI:
        return ChatOpenAI(
            base_url=settings.CHAT_LLM_BASE_URL,
            api_key=settings.CHAT_LLM_API_KEY,
            model=settings.CHAT_LLM_MODEL,
            temperature=temperature
        )

    @staticmethod
    def get_reasoning_llm(temperature: float = 0) -> ChatOpenAI:
        return ChatOpenAI(
            base_url=settings.REASONING_LLM_BASE_URL,
            api_key=settings.REASONING_LLM_API_KEY,
            model=settings.REASONING_LLM_MODEL,
            temperature=temperature
        )

    @staticmethod
    def get_embeddings() -> OpenAIEmbeddings:
        return OpenAIEmbeddings(
            base_url=settings.EMBEDDING_BASE_URL,
            api_key=settings.EMBEDDING_API_KEY,
            model=settings.EMBEDDING_MODEL,
            dimensions=settings.EMBEDDING_DIM
        )
