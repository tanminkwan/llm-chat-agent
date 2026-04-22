import pytest
from fastapi.testclient import TestClient
from apps.api.main import app
from libs.core.settings import settings

def test_get_config():
    """/api/config 엔드포인트가 .env/settings의 값을 정확히 반환하는지 테스트"""
    client = TestClient(app)
    response = client.get("/api/config")
    
    assert response.status_code == 200
    data = response.json()
    
    # settings에 설정된 값과 일치하는지 확인
    assert data["app_name"] == settings.APP_NAME
    assert data["chat_model"] == settings.CHAT_LLM_MODEL
    assert data["chat_label"] == settings.CHAT_LLM_LABEL
    assert data["reasoning_model"] == settings.REASONING_LLM_MODEL
    assert data["reasoning_label"] == settings.REASONING_LLM_LABEL

    # 비어있지 않은지 확인
    assert data["chat_label"] != ""
    assert data["reasoning_label"] != ""
