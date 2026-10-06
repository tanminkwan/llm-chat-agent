import re
from typing import List, Optional
from fastapi import HTTPException, Security, Depends, Request, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from authlib.integrations.starlette_client import OAuth
from jose import jwt, JWTError
import httpx

from libs.core.settings import settings
from libs.core.logging_helpers import set_virtual_user

# OAuth 및 OIDC 설정을 위한 Authlib 클라이언트
oauth = OAuth()
if not settings.NON_LOGIN_SERVICE:
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

# 가상 사용자 (통계 집계 기준). 비 로그인 모드에서만 요청 헤더로 지정 가능.
VIRTUAL_USER_HEADER = "X-Virtual-User"
VIRTUAL_USER_DESCRIPTION = (
    "가상 사용자 ID (로그·통계 집계용). 비 로그인 모드에서만 적용되며 "
    "IDP 모드에서는 무시되고 인증된 사용자로 기록됩니다."
)
_VIRTUAL_USER_PATTERN = re.compile(r"[A-Za-z0-9._@-]{1,64}")


def bind_virtual_user(user_sub: str, header_value: Optional[str]) -> None:
    """요청 context 에 가상 사용자를 바인딩한다.

    - 비 로그인 모드 + 헤더 지정: 헤더 값 (형식 오류 시 400)
    - 그 외 (IDP 모드, 헤더 미지정): 인증된 user_sub

    의존성을 함수로 직접 호출하면 header_value 에 Header() 기본값 객체가 들어오므로
    str 이 아닌 값은 미지정으로 취급한다.
    """
    if settings.NON_LOGIN_SERVICE and isinstance(header_value, str) and header_value:
        if not _VIRTUAL_USER_PATTERN.fullmatch(header_value):
            raise HTTPException(
                status_code=400,
                detail=f"{VIRTUAL_USER_HEADER} must match [A-Za-z0-9._@-]{{1,64}}",
            )
        set_virtual_user(header_value)
    else:
        set_virtual_user(user_sub)


async def get_current_user(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_virtual_user: Optional[str] = Header(
        None, alias=VIRTUAL_USER_HEADER, description=VIRTUAL_USER_DESCRIPTION
    ),
) -> UserInfo:
    """
    Bearer 토큰 또는 세션을 통해 사용자 정보를 반환하는 FastAPI Dependency.
    확정된 사용자 기준으로 가상 사용자를 요청 context 에 바인딩한다.
    """
    user = await _authenticate(request, cred)
    bind_virtual_user(user.sub, x_virtual_user)
    return user


async def _authenticate(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials],
) -> UserInfo:
    if settings.NON_LOGIN_SERVICE:
        return UserInfo(sub="nobody", username="nobody", groups=["Admin"])

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
