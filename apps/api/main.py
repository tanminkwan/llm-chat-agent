from fastapi import FastAPI, Depends, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

from libs.core.settings import settings
from libs.core.auth import oauth, get_current_user, UserInfo
from libs.core.llm import LLMGateway

app = FastAPI(title=settings.APP_NAME)

# OIDC 인증 과정에서 state 유지를 위해 세션 미들웨어 필수
# 실제 환경에서는 예측 불가능한 시크릿 키로 변경해야 함
app.add_middleware(SessionMiddleware, secret_key="REPLACE_ME_WITH_A_SECURE_SECRET")

@app.get("/")
async def root():
    return {"message": "LLM Agent API is running", "app_name": settings.APP_NAME}

# --- 인증 관련 엔드포인트 ---

@app.get("/login")
async def login(request: Request):
    """IDP 로그인 페이지로 리다이렉트"""
    redirect_uri = settings.OIDC_REDIRECT_URI
    return await oauth.create_client('mwm-idp').authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    """IDP로부터 인증 코드를 받아 토큰으로 교환"""
    client = oauth.create_client('mwm-idp')
    token = await client.authorize_access_token(request)
    # 실제로는 이 토큰을 세션이나 자체 JWT로 변환하여 브라우저에 반환해야 합니다.
    # 여기서는 테스트를 위해 토큰 정보를 그대로 반환합니다.
    return {"token_info": token}

# --- 에이전트 서비스 엔드포인트 ---

@app.get("/chat/test")
async def chat_test(user: UserInfo = Depends(get_current_user)):
    """인증된 사용자만 사용 가능한 LLM 응답 테스트"""
    llm = LLMGateway.get_chat_llm()
    response = await llm.ainvoke(f"Hello, I am {user.username}. What's your role?")
    return {"agent_response": response.content, "user": user.username}

if __name__ == "__main__":
    # HTTPS(21443 포트)로 서버 기동
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=21443,
        ssl_keyfile="certs/key.pem",
        ssl_certfile="certs/cert.pem",
        reload=True
    )
