from typing import List, Optional
from fastapi import HTTPException, Security, Depends
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

security = HTTPBearer()

class UserInfo:
    """사용자 정보 및 권한을 담는 클래스"""
    def __init__(self, sub: str, username: str, groups: List[str]):
        self.sub = sub
        self.username = username
        self.groups = groups
        self.is_admin = "Admin" in groups
        self.is_user = "User" in groups or self.is_admin

async def get_current_user(cred: HTTPAuthorizationCredentials = Security(security)) -> UserInfo:
    """
    Bearer 토큰을 검증하고 사용자 정보를 반환하는 FastAPI Dependency.
    """
    token = cred.credentials
    try:
        # 1. IDP의 JWKS(공개키)를 가져와 서명 검증
        # (실제 구현 시에는 JWKS를 캐싱하여 성능을 최적화해야 합니다)
        jwks_url = f"{settings.OIDC_ISSUER}/oauth/jwks"
        async with httpx.AsyncClient(verify=False) as client: # 내부망 .local 대응을 위해 verify=False 처리 가능성 고려
            response = await client.get(jwks_url)
            jwks = response.json()

        # 2. 토큰 디코딩 및 검증 (RS256)
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=settings.OIDC_CLIENT_ID,
            issuer=settings.OIDC_ISSUER
        )

        # 3. 필수 클레임 추출
        sub = payload.get("sub")
        username = payload.get("preferred_username")
        groups = payload.get("groups", [])

        if not sub or not username:
            raise HTTPException(status_code=401, detail="Invalid token claims")

        return UserInfo(sub=sub, username=username, groups=groups)

    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {str(e)}")

def admin_required(user: UserInfo = Depends(get_current_user)):
    """관리자 권한 확인을 위한 Dependency"""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
