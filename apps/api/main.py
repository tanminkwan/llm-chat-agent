from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
import uvicorn
import json

from libs.core.settings import settings
from libs.core.auth import oauth, get_current_user, UserInfo
from libs.core.llm import LLMGateway
from libs.core.memory import memory_manager
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import SystemMessage, HumanMessage

app = FastAPI(title=settings.APP_NAME)
app.add_middleware(SessionMiddleware, secret_key="REPLACE_ME_WITH_A_SECURE_SECRET")

@app.get("/")
async def root():
    return {"message": "LLM Agent API is running", "app_name": settings.APP_NAME}

# --- 인증 관련 엔드포인트 ---

@app.get("/login")
async def login(request: Request):
    redirect_uri = settings.OIDC_REDIRECT_URI
    return await oauth.create_client('mwm-idp').authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    client = oauth.create_client('mwm-idp')
    token = await client.authorize_access_token(request)
    return {"token_info": token}

# --- 채팅 엔드포인트 (Phase 2 핵심) ---

@app.post("/chat")
async def chat(
    request: Request,
    message: str,
    session_id: str = "default-session",
    user: UserInfo = Depends(get_current_user)
):
    """
    메모리와 권한 기반 시스템 프롬프트가 적용된 스트리밍 채팅 API
    """
    # 1. 권한별 시스템 프롬프트 결정
    if user.is_admin:
        system_content = "당신은 시스템 관리자 권한을 가진 강력한 AI 에이전트입니다. 보안 및 로그 분석에 특화되어 있습니다."
    else:
        system_content = "당신은 친절한 일반 사용자용 AI 에시스턴트입니다."

    # 2. 메모리 로드
    history = memory_manager.get_session_history(session_id)
    
    # 3. LLM 호출 및 스트리밍 응답 생성
    llm = LLMGateway.get_chat_llm()
    
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
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=21443,
        ssl_keyfile="certs/key.pem",
        ssl_certfile="certs/cert.pem",
        reload=True
    )
