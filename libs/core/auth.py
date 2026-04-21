from typing import List, Optional
from fastapi import HTTPException, Security, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from authlib.integrations.starlette_client import OAuth
from jose import jwt, JWTError
import httpx

from libs.core.settings import settings

# OAuth 및 OIDC 설정을 위한 Authlib 클라이언트
oauth = OAuth()
oauth.register(
    name='mwm-idp',
    client_id=settings.OIDC_CLIENT_ID,
    client_secret=settings.OIDC_CLIENT_SECRET,
    server_metadata_url=f"{settings.OIDC_ISSUER}/.well-known/openid-configuration",
    client_kwargs={
        'scope': 'openid profile email groups',
        'verify': False  # 자가 서명 인증서 허용
    }
)

security = HTTPBearer(auto_error=False)

class UserInfo:
    """사용자 정보 및 권한을 담는 클래스"""
    def __init__(self, sub: str, username: str, groups: List[str]):
        self.sub = sub
        self.username = username
        self.groups = groups
        self.is_admin = "Admin" in groups
        self.is_user = "User" in groups or self.is_admin

async def get_current_user(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> UserInfo:
    """
    Bearer 토큰 또는 세션을 통해 사용자 정보를 반환하는 FastAPI Dependency.
    """
    # 1. Bearer 토큰 확인
    if cred and cred.credentials and cred.credentials != "null":
        token = cred.credentials
        try:
            jwks_url = f"{settings.OIDC_ISSUER}/oauth/jwks"
            async with httpx.AsyncClient(verify=False) as client:
                response = await client.get(jwks_url)
                jwks = response.json()

            payload = jwt.decode(
                token, jwks, algorithms=["RS256"],
                audience=settings.OIDC_CLIENT_ID, issuer=settings.OIDC_ISSUER
            )
            return UserInfo(
                sub=payload.get("sub"),
                username=payload.get("preferred_username"),
                groups=payload.get("groups", [])
            )
        except Exception:
            pass # 토큰 검증 실패 시 세션 확인으로 넘어감

    # 2. 세션 확인 (브라우저 UI용)
    user_session = request.session.get('user')
    if user_session:
        print(f"DEBUG: Session found for user: {user_session.get('preferred_username')}")
        return UserInfo(
            sub=user_session.get("sub"),
            username=user_session.get("preferred_username"),
            groups=user_session.get("groups", [])
        )

    print("DEBUG: No session or bearer token found")
    raise HTTPException(status_code=401, detail="Authentication required")

def admin_required(user: UserInfo = Depends(get_current_user)):
    """관리자 권한 확인을 위한 Dependency"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
