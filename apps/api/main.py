from typing import Optional
from fastapi import FastAPI, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import uvicorn
import json
import os

from libs.core.settings import settings
from libs.core.auth import oauth, get_current_user, UserInfo
from libs.core.llm import LLMGateway
from libs.core.memory import memory_manager
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import SystemMessage, HumanMessage

app = FastAPI(title=settings.APP_NAME)
# 고정된 시크릿 키 사용 및 HTTPS 보안 설정 강화
app.add_middleware(
    SessionMiddleware, 
    secret_key="mwm-llm-agent-secure-session-key",
    https_only=True,
    same_site="lax"
)

# 정적 파일 경로 설정 (UI용)
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# --- 인증 관련 엔드포인트 ---

@app.get("/auth/login")
async def login(request: Request):
    """IDP 로그인 페이지로 리다이렉트"""
    redirect_uri = settings.OIDC_REDIRECT_URI
    return await oauth.create_client('mwm-idp').authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    """IDP로부터 인증 코드를 받아 토큰으로 교환 및 세션 저장"""
    client = oauth.create_client('mwm-idp')
    token = await client.authorize_access_token(request)
    user_info = token.get('userinfo')
    
    if user_info:
        groups = user_info.get('groups', [])
        print(f"DEBUG: User groups: {groups}")
        
        # 권한 확인: Admin 또는 User 그룹이 하나도 없는 경우
        if not any(role in groups for role in ["Admin", "User"]):
            print("DEBUG: Access denied - No valid role found")
            return RedirectResponse(url="/static/unauthorized.html")

        # 세션에 사용자 정보 저장
        request.session['user'] = user_info
        return RedirectResponse(url="/")
    
    print("DEBUG: No user_info found in token")
    return RedirectResponse(url="/static/unauthorized.html")

@app.get("/auth/logout")
async def logout(request: Request):
    """세션 초기화 및 로그아웃"""
    request.session.clear()
    return RedirectResponse(url="/auth/login")

@app.get("/")
async def root(request: Request):
    """메인 화면 접근 시 권한 체크"""
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url="/auth/login")
    
    groups = user.get('groups', [])
    if not any(role in groups for role in ["Admin", "User"]):
        return RedirectResponse(url="/static/unauthorized.html")
    
    # 권한이 있으면 index.html 제공
    from fastapi.responses import FileResponse
    return FileResponse("apps/api/static/index.html")

@app.get("/user/me")
async def get_me(request: Request):
    """현재 세션의 사용자 정보 반환"""
    user = request.session.get('user')
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

@app.get("/api/config")
async def get_config():
    """UI 설정을 위한 정보를 .env에서 동적으로 가져옴 (하드코딩 방지)"""
    return {
        "app_name": settings.APP_NAME,
        "chat_model": settings.CHAT_LLM_MODEL,
        "chat_label": settings.CHAT_LLM_LABEL,
        "reasoning_model": settings.REASONING_LLM_MODEL,
        "reasoning_label": settings.REASONING_LLM_LABEL
    }

# --- 채팅 엔드포인트 (Phase 2 핵심) ---

@app.post("/chat")
async def chat(
    message: str = Query(...),
    model_type: str = Query("chat"), # "chat" or "reasoning"
    system_prompt: Optional[str] = Query(None),
    user: UserInfo = Depends(get_current_user),
    session_id: Optional[str] = Query(None)
):
    # API 레벨에서도 최종 권한 확인
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="사용 권한이 없습니다. 관리자에게 문의하세요.")
    """
    메모리, 권한 프롬프트, 모델 선택이 적용된 스트리밍 채팅 API
    """
    # 1. 세션 ID 결정 (사용자별 독립 메모리 보장)
    actual_session_id = session_id or f"user_{user.sub}"
    
    # 2. 시스템 프롬프트 결정 (사용자 입력이 있으면 그것을 우선 사용)
    if system_prompt:
        system_content = system_prompt
    elif user.is_admin:
        system_content = "당신은 시스템 관리자 권한을 가진 AI입니다. 상세하고 전문적인 분석을 제공합니다."
    else:
        system_content = "당신은 친절한 일반 AI 어시스턴트입니다."

    # 3. 모델 선택 (chat vs reasoning)
    if model_type == "reasoning":
        llm = LLMGateway.get_reasoning_llm()
    else:
        llm = LLMGateway.get_chat_llm()
    
    # 4. 메모리 로드
    history = memory_manager.get_session_history(actual_session_id)
    
    async def event_generator():
        full_response = ""
        # 시스템 메시지 + 히스토리 + 현재 메시지 구성
        messages = [SystemMessage(content=system_content)] + history.messages + [HumanMessage(content=message)]
        
        async for chunk in llm.astream(messages):
            content = chunk.content
            full_response += content
            yield f"data: {json.dumps({'content': content})}\n\n"
        
        # 4. 대화 종료 후 메모리에 저장
        history.add_user_message(message)
        history.add_ai_message(full_response)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    # Nginx SSL Termination 환경에 맞춰 HTTP(8000)로 기동
    uvicorn.run(
        "apps.api.main:app", 
        host="0.0.0.0", 
        port=8000,
        reload=settings.DEBUG
    )
