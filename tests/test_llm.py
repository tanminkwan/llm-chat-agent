import pytest
from unittest.mock import patch, MagicMock
from libs.core.llm import LLMGateway, CustomProductionEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.embeddings import Embeddings
from libs.core.settings import settings

def test_llm_gateway_initialization():
    chat_llm = LLMGateway.get_chat_llm()
    assert isinstance(chat_llm, ChatOpenAI)
    
    reasoning_llm = LLMGateway.get_reasoning_llm()
    assert isinstance(reasoning_llm, ChatOpenAI)
    
    # 1. 기본 케이스 (OpenAI)
    settings.EMBEDDING_USE_CUSTOM = False
    embeddings = LLMGateway.get_embeddings()
    assert isinstance(embeddings, Embeddings)
    # 기본 설정에서는 OpenAIEmbeddings여야 함 (이미 OpenAIEmbeddings는 Embeddings의 하위 클래스)
    assert "OpenAIEmbeddings" in str(type(embeddings))

def test_custom_embedding_branching():
    # 2. 커스텀 케이스 분기 테스트
    settings.EMBEDDING_USE_CUSTOM = True
    settings.EMBEDDING_BASE_URL = "http://mock-server"
    embeddings = LLMGateway.get_embeddings()
    assert isinstance(embeddings, CustomProductionEmbeddings)
    
    # 원복
    settings.EMBEDDING_USE_CUSTOM = False

@pytest.mark.asyncio
async def test_custom_embedding_mock_server():
    """가상 서버(Mock)를 통한 커스텀 API 규격 검증"""
    mock_embeddings = [[0.1, 0.2, 0.3]]
    mock_response_data = {"embeddings": mock_embeddings, "dim": 3, "count": 1}
    
    settings.EMBEDDING_USE_CUSTOM = True
    settings.EMBEDDING_BASE_URL = "http://mock-server"
    
    custom_embeddings = LLMGateway.get_embeddings()
    
    # [동기 테스트]
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp
        
        result = custom_embeddings.embed_query("테스트 문장")
        
        assert result == mock_embeddings[0]
        # 사용자님이 알려주신 "tests" 키를 정확히 사용하는지 확인
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {"tests": ["테스트 문장"]}

    # [비동기 테스트]
    with patch("httpx.AsyncClient.post") as mock_apost:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.raise_for_status.return_value = None
        
        # 비동기 응답 시뮬레이션
        async def mock_post_coro(*args, **kwargs):
            return mock_resp
        mock_apost.side_effect = mock_post_coro
        
        result = await custom_embeddings.aembed_query("테스트 문장")
        
        assert result == mock_embeddings[0]
        _, kwargs = mock_apost.call_args
        assert kwargs["json"] == {"tests": ["테스트 문장"]}

    # 설정 원복
    settings.EMBEDDING_USE_CUSTOM = False
