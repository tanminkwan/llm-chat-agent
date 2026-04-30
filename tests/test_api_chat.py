import pytest
import json
from unittest.mock import MagicMock, patch

# 인프라 연결 시도 차단
with patch("authlib.integrations.starlette_client.OAuth.register"), \
     patch("sqlalchemy.ext.asyncio.create_async_engine"):
    from apps.api.main import app, get_current_user as main_get_current_user
    from apps.api.schemas import UserInfo

from fastapi.testclient import TestClient

# 인증 모킹
def mock_get_current_user():
    return UserInfo(sub="test_sub", preferred_username="test_user", groups=["Admin"])

@pytest.fixture
def client():
    # main.py에 정의된 get_current_user를 오버라이드
    app.dependency_overrides[main_get_current_user] = mock_get_current_user
    with patch("apps.api.main.engine.begin"):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()

@patch("libs.core.llm.LLMGateway.get_chat_llm")
def test_chat_post_body_success(mock_get_llm, client):
    """POST Body 방식으로 메시지가 정상 전달되는지 테스트"""
    mock_llm = MagicMock()
    async def mock_astream(messages):
        assert messages[0].content == "You are a specialized assistant."
        assert messages[-1].content == "Hello, AI!"
        yield MagicMock(content="Hi there!")
    
    mock_llm.astream = mock_astream
    mock_get_llm.return_value = mock_llm

    response = client.post(
        "/chat",
        json={
            "message": "Hello, AI!",
            "system_prompt": "You are a specialized assistant.",
            "temperature": 0.8
        }
    )
    
    assert response.status_code == 200
    assert "Hi there!" in response.text

@patch("libs.core.llm.LLMGateway.get_chat_llm")
def test_chat_large_payload_success(mock_get_llm, client):
    """URL 제한을 초과하는 대용량 메시지(30KB) 전송 테스트"""
    large_message = "A" * 30000
    
    mock_llm = MagicMock()
    async def mock_astream(messages):
        assert len(messages[-1].content) == 30000
        yield MagicMock(content="Got your long message!")
    
    mock_llm.astream = mock_astream
    mock_get_llm.return_value = mock_llm

    response = client.post(
        "/chat",
        json={
            "message": large_message,
            "model_type": "chat"
        }
    )
    
    assert response.status_code == 200
    assert "Got your long message!" in response.text

def test_chat_validation_error(client):
    """필수 필드 누락 시 유효성 검사 에러(422) 발생 테스트"""
    response = client.post(
        "/chat",
        json={
            "model_type": "chat"
        }
    )
    
    assert response.status_code == 422
