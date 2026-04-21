import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from libs.core.auth import get_current_user, UserInfo
from jose import jwt

# 테스트용 더미 데이터
ALGORITHM = "RS256"
MOCK_PAYLOAD = {
    "sub": "user123",
    "preferred_username": "testuser",
    "groups": ["User"],
    "iss": "https://idp.mwm.local:20443",
    "aud": "your-client-id"
}

@pytest.fixture
def mock_creds():
    class MockCreds:
        credentials = "dummy_token"
    return MockCreds()

@pytest.mark.asyncio
@patch("libs.core.auth.jwt.decode")
@patch("httpx.AsyncClient.get")
async def test_get_current_user_success(mock_get, mock_decode, mock_creds):
    # Mock 설정
    mock_get.return_value = AsyncMock(json=lambda: {"keys": []})
    mock_decode.return_value = MOCK_PAYLOAD
    
    # 실행
    user = await get_current_user(mock_creds)
    
    # 검증
    assert user.username == "testuser"
    assert user.is_user is True
    assert user.is_admin is False

@pytest.mark.asyncio
@patch("libs.core.auth.jwt.decode")
@patch("httpx.AsyncClient.get")
async def test_get_current_user_admin(mock_get, mock_decode, mock_creds):
    # Admin 권한 페이로드
    admin_payload = MOCK_PAYLOAD.copy()
    admin_payload["groups"] = ["Admin", "User"]
    mock_get.return_value = AsyncMock(json=lambda: {"keys": []})
    mock_decode.return_value = admin_payload
    
    user = await get_current_user(mock_creds)
    
    assert user.is_admin is True

@pytest.mark.asyncio
@patch("libs.core.auth.jwt.decode")
@patch("httpx.AsyncClient.get")
async def test_get_current_user_invalid_token(mock_get, mock_decode, mock_creds):
    # JWKS 모킹
    mock_get.return_value = AsyncMock(json=lambda: {"keys": []})
    # 토큰 디코딩 실패 시뮬레이션
    from jose import JWTError
    mock_decode.side_effect = JWTError("Invalid token")
    
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user(mock_creds)
    
    assert excinfo.value.status_code == 401
