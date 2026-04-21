import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from apps.api.main import app
from libs.core.auth import UserInfo, get_current_user

# 인증 모킹 (Admin 유저로 가정)
def mock_get_current_user():
    return UserInfo(sub="test_sub", username="test_user", groups=["Admin"])

@patch("libs.core.llm.LLMGateway.get_chat_llm")
def test_chat_with_custom_system_prompt(mock_get_llm):
    """사용자 정의 시스템 프롬프트가 적용되는지 테스트"""
    # 테스트 전용 오버라이드 적용
    app.dependency_overrides[get_current_user] = mock_get_current_user
    test_client = TestClient(app)
    
    # LLM 모킹
    mock_llm = MagicMock()
    # astream은 비동기 제너레이터이므로 적절히 모킹
    async def mock_astream(messages):
        # 전달된 메시지 중 첫 번째가 시스템 프롬프트인지 확인
        assert messages[0].content == "You are a specialized translator."
        yield MagicMock(content="Hello")
    
    mock_llm.astream = mock_astream
    mock_get_llm.return_value = mock_llm

    # API 호출
    response = test_client.post(
        "/chat",
        params={
            "message": "Hi",
            "system_prompt": "You are a specialized translator."
        }
    )
    
    assert response.status_code == 200
    # 스트리밍 응답이므로 내용을 확인하려면 제너레이터를 읽어야 함
    assert "Hello" in response.text

app.dependency_overrides.clear()
