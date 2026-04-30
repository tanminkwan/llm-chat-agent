import os
import json
from typing import Optional, List
from fastapi import FastAPI, Depends, Request, HTTPException, Query, Path, Body
from fastapi.responses import StreamingResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
import uvicorn
import pandas as pd
import io
import uuid
from fastapi import BackgroundTasks, File, UploadFile, Form
import logging
import sys

from libs.core.settings import settings
from libs.core.llm import LLMGateway

# 로깅 설정
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("llm-chat-agent")

from libs.core.memory import memory_manager
from libs.core.database import get_db, engine, Base, AsyncSessionLocal
from libs.core.service import RAGService

app = FastAPI(
    title=settings.APP_NAME,
    docs_url=None,   # CDN 의존성 제거를 위해 기본 경로 비활성화
    redoc_url=None
)

# 세션 미들웨어 설정 (OIDC 상태값 저장용)
app.add_middleware(SessionMiddleware, secret_key=settings.OIDC_CLIENT_SECRET)

# OAuth 설정
oauth = OAuth()
oauth.register(
    name='mwm-idp',
    client_id=settings.OIDC_CLIENT_ID,
    client_secret=settings.OIDC_CLIENT_SECRET,
    server_metadata_url=f"{settings.OIDC_ISSUER}/.well-known/openid-configuration",
    client_kwargs={'scope': 'openid profile email groups', 'verify': False}
)

# 정적 파일 경로 설정 (UI용)
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# --- Offline API Docs (Rule 5.26 준수) ---

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger/swagger-ui.css",
        swagger_favicon_url="/static/swagger/favicon.png",
    )

@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=app.title + " - ReDoc",
        redoc_js_url="/static/swagger/redoc.standalone.js",
        redoc_favicon_url="/static/swagger/favicon.png",
    )

# 데이터베이스 테이블 생성 (Startup 시)
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

from .schemas import (
    CollectionCreate, CollectionRead, DomainCreate, DomainRead,
    KnowledgeCreate, SearchResult, SearchRequest, UserInfo, ConfigResponse,
    MessageResponse, TaskStatusResponse, DeleteCountResponse, ChatRequest
)
# --- 인증 관련 엔드포인트 ---

@app.get("/auth/login", tags=["Auth"], summary="IDP 로그인")
async def login(request: Request):
    """IDP 로그인 페이지로 리다이렉트하여 인증을 시작합니다."""
    redirect_uri = settings.OIDC_REDIRECT_URI
    return await oauth.create_client('mwm-idp').authorize_redirect(request, redirect_uri)

@app.get("/auth/callback", tags=["Auth"], summary="IDP 콜백")
async def auth_callback(request: Request):
    """IDP 인증 완료 후 토큰을 처리하고 세션을 생성합니다."""
    token = await oauth.create_client('mwm-idp').authorize_access_token(request)
    user_info = token.get('userinfo')
    
    if user_info:
        groups = user_info.get('groups', [])
        if not any(role in groups for role in ["Admin", "User"]):
            return RedirectResponse(url="/static/unauthorized.html")

        request.session['user'] = user_info
        return RedirectResponse(url="/")
    
    return RedirectResponse(url="/static/unauthorized.html")

@app.get("/auth/logout", tags=["Auth"], summary="로그아웃")
async def logout(request: Request):
    """서버 세션을 초기화하고 로그인 페이지로 보냅니다."""
    request.session.clear()
    return RedirectResponse(url="/auth/login")

# --- SPA UI 라우트 ---
# 모든 UI 라우트는 동일한 SPA 셸(index.html)을 반환한다.
# 클라이언트 측 라우터(app.js)가 URL 경로를 기준으로 알맞은 뷰를 표시하므로,
# 직접 URL로 접근하거나 새로고침해도 의도한 화면이 그려진다.
SPA_INDEX_PATH = os.path.join(static_dir, "index.html")


def _require_user(request: Request, admin_only: bool = False):
    """세션 기반 인증 체크. 인증 실패 시 RedirectResponse, 통과 시 None."""
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url="/auth/login")

    groups = user.get('groups', [])
    if admin_only:
        if "Admin" not in groups:
            return RedirectResponse(url="/static/unauthorized.html")
    else:
        if not any(role in groups for role in ["Admin", "User"]):
            return RedirectResponse(url="/static/unauthorized.html")
    return None


@app.get("/", tags=["UI"], summary="SPA 진입점 - Chat 화면")
async def root(request: Request):
    """SPA 메인 진입점. 클라이언트 라우터가 Chat 뷰를 렌더링한다."""
    redirect = _require_user(request)
    if redirect:
        return redirect
    return FileResponse(SPA_INDEX_PATH)


@app.get("/rag", tags=["UI"], summary="SPA - RAG 콘솔 화면")
async def rag_console(request: Request):
    """동일한 SPA 셸을 반환. 클라이언트 라우터가 RAG 뷰를 활성화한다."""
    redirect = _require_user(request)
    if redirect:
        return redirect
    return FileResponse(SPA_INDEX_PATH)


@app.get("/admin", tags=["UI"], summary="SPA - 관리자 대시보드")
async def admin_console(request: Request):
    """동일한 SPA 셸을 반환. Admin 그룹만 접근 가능."""
    redirect = _require_user(request, admin_only=True)
    if redirect:
        return redirect
    return FileResponse(SPA_INDEX_PATH)

async def get_current_user(request: Request) -> UserInfo:
    """세션에서 사용자 정보를 가져오는 의존성 주입 함수"""
    user = request.session.get('user')
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserInfo(**user)

@app.get("/user/me", tags=["System"], response_model=UserInfo, summary="현재 사용자 정보 조회")
async def get_me(user: UserInfo = Depends(get_current_user)):
    """현재 로그인된 사용자의 ID, 이름, 권한(그룹) 정보를 반환합니다."""
    return user

@app.get("/api/config", tags=["System"], response_model=ConfigResponse, summary="UI 설정 정보 조회")
async def get_config():
    """서버의 앱 이름, LLM 모델 설정 등 프론트엔드 렌더링에 필요한 환경 변수를 반환합니다."""
    return {
        "app_name": settings.APP_NAME,
        "chat_model": settings.CHAT_LLM_MODEL,
        "chat_label": settings.CHAT_LLM_LABEL,
        "reasoning_model": settings.REASONING_LLM_MODEL,
        "reasoning_label": settings.REASONING_LLM_LABEL
    }

# --- RAG 관리 엔드포인트 (User 허용 기능) ---

def get_rag_service(db: AsyncSession = Depends(get_db)) -> RAGService:
    return RAGService(db)

@app.post("/api/collections", tags=["RAG Management"], response_model=CollectionRead, summary="콜렉션 생성")
async def create_collection(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user),
    data: CollectionCreate = Body(...)
):
    """새로운 지식 콜렉션(벡터 공간)을 생성합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.create_collection(**data.model_dump())

@app.get("/api/collections", tags=["RAG Management"], response_model=List[CollectionRead], summary="콜렉션 목록 조회")
async def list_collections(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """현재 시스템에 등록된 모든 콜렉션 정보를 가져옵니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.list_collections()

@app.put("/api/collections/{collection_name}", tags=["RAG Management"], response_model=CollectionRead, summary="콜렉션 정보 수정")
async def update_collection(
    collection_name: str = Path(..., description="수정할 콜렉션 ID"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user),
    data: CollectionCreate = Body(...)
):
    """콜렉션의 표시 이름, 설명, 검색 방식 등을 수정합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        update_data = data.model_dump()
        update_data.pop("collection_name", None)
        return await service.update_collection(collection_name, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.delete("/api/collections/{collection_name}", tags=["RAG Management"], response_model=MessageResponse, summary="콜렉션 삭제")
async def delete_collection(
    collection_name: str = Path(..., description="삭제할 콜렉션 ID"),
    delete_vector: bool = Query(False, description="True 설정 시 Qdrant 벡터 데이터까지 완전히 삭제합니다."),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """콜렉션 메타데이터(DB)와 물리적 데이터(Qdrant)를 삭제합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        await service.delete_collection(collection_name, delete_vector)
        return {"message": "Collection deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/domains", tags=["RAG Management"], response_model=DomainRead, summary="도메인(분류) 생성")
async def create_domain(
    data: DomainCreate,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """지식을 그룹화할 도메인(분류)을 생성합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.create_domain(name=data.name)

@app.get("/api/domains", tags=["RAG Management"], response_model=List[DomainRead], summary="도메인 목록 조회")
async def list_domains(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """현재 등록된 모든 도메인 목록을 가져옵니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.list_domains()

@app.put("/api/domains/{dom_id}", tags=["RAG Management"], response_model=DomainRead, summary="도메인 정보 수정")
async def update_domain(
    dom_id: int = Path(..., description="수정할 도메인 고유 번호"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user),
    data: DomainCreate = Body(...)
):
    """도메인 명칭을 수정합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        return await service.update_domain(dom_id, name=data.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.delete("/api/domains/{dom_id}", tags=["RAG Management"], response_model=MessageResponse, summary="도메인 삭제")
async def delete_domain(
    dom_id: int = Path(..., description="삭제할 도메인 고유 번호"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """도메인을 삭제하고, 해당 도메인에 속한 모든 지식 데이터를 전체 콜렉션에서 제거합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        await service.delete_domain(dom_id)
        return {"message": "Domain deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# --- RAG 지식 데이터 관리 API ---

@app.post("/api/rag/search", tags=["RAG Data"], response_model=List[SearchResult], summary="통합 RAG 검색 (JSON Body)")
async def search_rag(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user),
    request: SearchRequest = Body(...)
):
    """지정된 콜렉션에서 쿼리와 가장 유사한 지식 조각들을 검색하여 점수 순으로 반환합니다."""
    request_id = str(uuid.uuid4())[:8]
    user_id = user.sub

    # [LLM_LOG] RAG 검색 요청 로깅
    search_request_log = {
        "request_id": request_id,
        "user_id": user_id,
        "type": "rag_search_request",
        "collection_id": request.collection_id,
        "domain_id": request.domain_id,
        "query": request.query,
        "search_method": request.search_method
    }
    logger.debug(f"[LLM_LOG] {json.dumps(search_request_log, ensure_ascii=False)}")

    results = await service.search_rag(
        collection_id=request.collection_id,
        domain_id=request.domain_id,
        query=request.query,
        search_method=request.search_method,
        limit=request.limit
    )

    # [LLM_LOG] RAG 검색 결과 로깅 (메타데이터만)
    search_response_log = {
        "request_id": request_id,
        "user_id": user_id,
        "type": "rag_search_response",
        "results_count": len(results),
        "results_metadata": [
            {
                "id": r.get("id"),
                "score": r.get("score"),
                "collection": r.get("collection"),
                "domain_id": r.get("domain_id"),
                "source": r.get("source"),
                "created_at": r.get("created_at")
            } for r in results
        ]
    }
    logger.debug(f"[LLM_LOG] {json.dumps(search_response_log, ensure_ascii=False)}")

    return results

@app.post("/api/rag/knowledge", tags=["RAG Data"], summary="개별 지식 등록/수정")
async def add_knowledge(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user),
    data: KnowledgeCreate = Body(...)
):
    """단일 지식 데이터를 등록합니다. point_id를 포함하면 기존 데이터를 수정(Upsert)합니다."""
    try:
        return await service.add_knowledge_point(**data.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/rag/knowledge/{collection_name}/{point_id}", tags=["RAG Data"], response_model=MessageResponse, summary="개별 지식 삭제")
async def delete_knowledge(
    collection_name: str = Path(..., description="데이터가 속한 콜렉션 ID"),
    point_id: str = Path(..., description="삭제할 데이터 고유 ID"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """콜렉션 내의 특정 지식 데이터(포인트) 하나를 삭제합니다."""
    return await service.delete_knowledge_point(collection_name, point_id)

@app.get("/api/rag/delete-count", tags=["RAG Data"], response_model=DeleteCountResponse, summary="삭제 대상 건수 확인")
async def get_delete_count(
    collection: str = Query(..., description="대상 콜렉션 ID"),
    domain_id: Optional[int] = Query(None, description="도메인 필터"),
    source: Optional[str] = Query(None, description="출처(파일명 등) 필터"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """일괄 삭제를 실행하기 전, 필터링 조건에 부합하는 데이터의 총 개수를 확인합니다."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    count = await service.count_knowledge_points(collection, domain_id, source)
    return {"count": count}

@app.delete("/api/rag/bulk-delete", tags=["RAG Data"], response_model=MessageResponse, summary="조건부 일괄 삭제")
async def bulk_delete_knowledge(
    collection: str = Query(..., description="대상 콜렉션 ID"),
    domain_id: Optional[int] = Query(None, description="도메인 필터"),
    source: Optional[str] = Query(None, description="출처 필터"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """도메인 또는 출처(파일명) 조건에 맞는 지식 데이터를 해당 콜렉션에서 대량 삭제합니다."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    try:
        return await service.bulk_delete_knowledge_points(collection, domain_id, source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- 채팅 엔드포인트 ---

@app.post("/chat", tags=["Chat"], summary="LLM 대화 (Streaming)")
async def chat(
    user: UserInfo = Depends(get_current_user),
    request: ChatRequest = Body(...)
):
    """LLM과 실시간 대화를 수행하며, SSE(Server-Sent Events) 방식으로 응답을 스트리밍합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="사용 권한이 없습니다.")

    actual_thread_id = request.thread_id or f"user_{user.sub}"
    
    if request.model_type == "reasoning":
        llm = LLMGateway.get_reasoning_llm(temperature=request.temperature)
    else:
        llm = LLMGateway.get_chat_llm(temperature=request.temperature)
    
    history = memory_manager.get_thread_history(actual_thread_id)
    
    async def event_generator():
        full_response = ""
        request_id = str(uuid.uuid4())[:8] # 고유 요청 ID (Trace ID)
        user_id = user.sub
        system_prompt = request.system_prompt or "당신은 AI 어시스턴트입니다."
        messages = [SystemMessage(content=system_prompt)] + history.messages + [HumanMessage(content=request.message)]
        
        # [LLM_LOG] JSON 구조화 로깅 (요청)
        request_log = {
            "request_id": request_id,
            "user_id": user_id,
            "type": "request",
            "thread_id": actual_thread_id,
            "model_type": request.model_type,
            "messages": [{"role": msg.type, "content": msg.content} for msg in messages]
        }
        logger.debug(f"[LLM_LOG] {json.dumps(request_log, ensure_ascii=False)}")

        async def stream_with_logging():
            nonlocal full_response
            try:
                async for chunk in llm.astream(messages):
                    content = chunk.content
                    if content:
                        full_response += content
                        yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
            except Exception as e:
                error_log = {
                    "request_id": request_id,
                    "thread_id": actual_thread_id,
                    "user_id": user_id,
                    "type": "error",
                    "error": str(e)
                }
                logger.error(f"[LLM_LOG] {json.dumps(error_log, ensure_ascii=False)}")
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        async for event in stream_with_logging():
            yield event
        
        # [LLM_LOG] JSON 구조화 로깅 (응답)
        response_log = {
            "request_id": request_id,
            "thread_id": actual_thread_id,
            "user_id": user_id,
            "type": "response",
            "full_response": full_response
        }
        logger.debug(f"[LLM_LOG] {json.dumps(response_log, ensure_ascii=False)}")

        history.add_user_message(request.message)
        history.add_ai_message(full_response)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# --- Excel Batch Upload 엔드포인트 ---

global_bulk_tasks = {}

async def process_bulk_upload_task(task_id: str, file_content: bytes, filename: str, collection_name: str, domain_id: int):
    success = 0
    error = 0
    errors_list = []
    
    try:
        df = pd.read_excel(io.BytesIO(file_content))
        total = len(df)
        global_bulk_tasks[task_id]['total'] = total
        
        async with AsyncSessionLocal() as db:
            service = RAGService(db)
            
            # 콜렉션 정보 가져와서 snippet_size_limit 확인
            col = await service.col_repo.get_by_id(collection_name)
            snippet_size_limit = col.snippet_size_limit if col else 500
            
            for index, row in df.iterrows():
                try:
                    content = str(row.get('Content', '')).strip()
                    ext_content = str(row.get('Extended Content', '')).strip()
                    if ext_content == 'nan' or not ext_content:
                        ext_content = content
                    
                    if len(content.encode('utf-8')) > snippet_size_limit:
                        raise ValueError(f"Content exceeds snippet size limit ({snippet_size_limit} bytes)")
                    
                    if not content or content == 'nan':
                        raise ValueError("Content is empty")
                    
                    await service.add_knowledge_point(
                        collection_name=collection_name,
                        domain_id=domain_id,
                        content=content,
                        extended_content=ext_content,
                        source=filename
                    )
                    success += 1
                except Exception as e:
                    error += 1
                    row_dict = row.to_dict()
                    row_dict['Error Reason'] = str(e)
                    errors_list.append(row_dict)
                    
                global_bulk_tasks[task_id]['success'] = success
                global_bulk_tasks[task_id]['error'] = error
                
        if errors_list:
            error_df = pd.DataFrame(errors_list)
            error_path = f"/tmp/{task_id}_errors.xlsx"
            error_df.to_excel(error_path, index=False)
            global_bulk_tasks[task_id]['error_file'] = error_path
            
    except Exception as e:
        print(f"Bulk task error: {e}")
        global_bulk_tasks[task_id]['error'] += 1
        
    global_bulk_tasks[task_id]['done'] = True

@app.get("/bulk", tags=["UI"], summary="SPA - 엑셀 일괄 업로드 화면")
async def bulk_page(request: Request):
    """동일한 SPA 셸을 반환. 클라이언트 라우터가 Bulk 뷰를 활성화한다."""
    redirect = _require_user(request)
    if redirect:
        return redirect
    return FileResponse(SPA_INDEX_PATH)

@app.post("/api/rag/bulk-upload", tags=["Bulk Operations"], summary="엑셀 일괄 업로드 시작")
async def start_bulk_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="지식 데이터가 담긴 엑셀 파일 (.xlsx)"),
    collection: str = Form(..., description="대상 콜렉션 ID"),
    domain_id: int = Form(..., description="대상 도메인 ID"),
    user: UserInfo = Depends(get_current_user)
):
    """엑셀 파일을 업로드하여 백그라운드에서 지식 데이터를 일괄 등록합니다. task_id를 반환합니다."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
        
    content = await file.read()
    task_id = str(uuid.uuid4())
    
    global_bulk_tasks[task_id] = {
        "total": 0,
        "success": 0,
        "error": 0,
        "done": False,
        "error_file": None
    }
    
    background_tasks.add_task(process_bulk_upload_task, task_id, content, file.filename, collection, domain_id)
    return {"task_id": task_id}

@app.get("/api/rag/bulk-progress/{task_id}", tags=["Bulk Operations"], response_model=TaskStatusResponse, summary="일괄 업로드 진행 상태 조회")
async def get_bulk_progress(task_id: str = Path(..., description="업로드 시작 시 발급받은 작업 ID")):
    """백그라운드에서 실행 중인 엑셀 업로드 작업의 진행 건수와 성공/실패 여부를 확인합니다."""
    task = global_bulk_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.get("/api/rag/bulk-error-download/{task_id}", tags=["Bulk Operations"], summary="업로드 실패 내역 다운로드")
async def download_bulk_errors(task_id: str = Path(..., description="작업 ID")):
    """업로드 과정에서 발생한 실패 데이터와 사유가 적힌 엑셀 파일을 다운로드합니다."""
    task = global_bulk_tasks.get(task_id)
    if not task or not task.get('error_file'):
        raise HTTPException(status_code=404, detail="Error file not found")
    return FileResponse(task['error_file'], filename=f"error_report_{task_id}.xlsx")

if __name__ == "__main__":
    uvicorn.run("apps.api.main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
