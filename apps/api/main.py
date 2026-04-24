import os
import json
from typing import Optional, List
from fastapi import FastAPI, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse, RedirectResponse
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

from libs.core.settings import settings
from libs.core.llm import LLMGateway
from libs.core.memory import memory_manager
from libs.core.database import get_db, engine, Base, AsyncSessionLocal
from libs.core.service import RAGService

app = FastAPI(title=settings.APP_NAME)

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

# 데이터베이스 테이블 생성 (Startup 시)
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- Pydantic Schemas ---
class CollectionCreate(BaseModel):
    collection_name: str = Field(..., pattern="^[a-zA-Z0-9_-]+$")  # 영문, 숫자, -, _ 만 허용
    name: str             # UI 표시용 명칭
    description: Optional[str] = None
    snippet_size_limit: int = 500
    search_method: str = "vector"

class DomainCreate(BaseModel):
    name: str

class KnowledgeCreate(BaseModel):
    collection_name: str
    domain_id: int
    content: str
    extended_content: str
    source: str
    point_id: Optional[str] = None

class UserInfo(BaseModel):
    sub: str
    preferred_username: str
    groups: List[str]
    
    @property
    def is_admin(self) -> bool:
        return "Admin" in self.groups

# --- 인증 관련 엔드포인트 ---

@app.get("/auth/login")
async def login(request: Request):
    """IDP 로그인 페이지로 리다이렉트"""
    redirect_uri = settings.OIDC_REDIRECT_URI
    return await oauth.create_client('mwm-idp').authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    """IDP 로그인 성공 후 콜백 처리"""
    token = await oauth.create_client('mwm-idp').authorize_access_token(request)
    user_info = token.get('userinfo')
    
    if user_info:
        groups = user_info.get('groups', [])
        if not any(role in groups for role in ["Admin", "User"]):
            return RedirectResponse(url="/static/unauthorized.html")

        request.session['user'] = user_info
        return RedirectResponse(url="/")
    
    return RedirectResponse(url="/static/unauthorized.html")

@app.get("/auth/logout")
async def logout(request: Request):
    """세션 초기화 및 로그아웃"""
    request.session.clear()
    return RedirectResponse(url="/auth/login")

@app.get("/")
async def root(request: Request):
    """메인 화면 접근 시 권한 체크"""
    # [진단 로그]
    import os
    path = "apps/api/static/index.html"
    exists = os.path.exists(path)
    contains_rag = False
    if exists:
        with open(path, "r") as f:
            contains_rag = "rag" in f.read()
    print(f"DEBUG: CWD={os.getcwd()}, Path={path}, Exists={exists}, Contains 'rag'={contains_rag}")

    user = request.session.get('user')
    if not user:
        return RedirectResponse(url="/auth/login")
    
    groups = user.get('groups', [])
    if not any(role in groups for role in ["Admin", "User"]):
        return RedirectResponse(url="/static/unauthorized.html")
    
    return FileResponse(path)

@app.get("/rag")
async def rag_console(request: Request):
    """RAG 지식 관리 콘솔 접근 (전체 화면)"""
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url="/auth/login")
    
    groups = user.get('groups', [])
    if not any(role in groups for role in ["Admin", "User"]):
        return RedirectResponse(url="/static/unauthorized.html")
    
    return FileResponse("apps/api/static/rag.html")

@app.get("/admin")
async def admin_console(request: Request):
    """관리자 대시보드 접근 (전체 화면)"""
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url="/auth/login")
    
    groups = user.get('groups', [])
    if "Admin" not in groups:
        return RedirectResponse(url="/static/unauthorized.html")
    
    return FileResponse("apps/api/static/admin.html")

async def get_current_user(request: Request) -> UserInfo:
    """세션에서 사용자 정보를 가져오는 의존성 주입 함수"""
    user = request.session.get('user')
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserInfo(**user)

@app.get("/user/me")
async def get_me(user: UserInfo = Depends(get_current_user)):
    """현재 세션의 사용자 정보 반환"""
    return user

@app.get("/api/config")
async def get_config():
    """UI 설정을 위한 정보를 .env에서 동적으로 가져옴"""
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

@app.post("/api/collections")
async def create_collection(
    data: CollectionCreate,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.create_collection(**data.model_dump())

@app.get("/api/collections")
async def list_collections(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.list_collections()

@app.put("/api/collections/{collection_name}")
async def update_collection(
    collection_name: str,
    data: CollectionCreate,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        update_data = data.model_dump()
        update_data.pop("collection_name", None)
        return await service.update_collection(collection_name, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.delete("/api/collections/{collection_name}")
async def delete_collection(
    collection_name: str,
    delete_vector: bool = Query(False),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        await service.delete_collection(collection_name, delete_vector)
        return {"message": "Collection deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/domains")
async def create_domain(
    data: DomainCreate,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.create_domain(name=data.name)

@app.get("/api/domains")
async def list_domains(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.list_domains()

@app.put("/api/domains/{dom_id}")
async def update_domain(
    dom_id: int,
    data: DomainCreate,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        return await service.update_domain(dom_id, name=data.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.delete("/api/domains/{dom_id}")
async def delete_domain(
    dom_id: int,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        await service.delete_domain(dom_id)
        return {"message": "Domain deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# --- RAG 지식 데이터 관리 API ---

@app.get("/api/rag/search")
async def search_rag(
    collection_id: Optional[str] = Query(None),
    domain_id: Optional[int] = Query(None),
    query: Optional[str] = Query(None),
    search_method: str = Query("vector"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    return await service.search_rag(
        collection_id=collection_id,
        domain_id=domain_id,
        query=query,
        search_method=search_method
    )

@app.post("/api/rag/knowledge")
async def add_knowledge(
    data: KnowledgeCreate,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    try:
        return await service.add_knowledge_point(**data.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/rag/knowledge/{collection_name}/{point_id}")
async def delete_knowledge(
    collection_name: str,
    point_id: str,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    return await service.delete_knowledge_point(collection_name, point_id)

@app.get("/api/rag/delete-count")
async def get_delete_count(
    collection: str = Query(...),
    domain_id: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    count = await service.count_knowledge_points(collection, domain_id, source)
    return {"count": count}

@app.delete("/api/rag/bulk-delete")
async def bulk_delete_knowledge(
    collection: str = Query(...),
    domain_id: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    try:
        return await service.bulk_delete_knowledge_points(collection, domain_id, source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- 채팅 엔드포인트 ---

@app.post("/chat")
async def chat(
    message: str = Query(...),
    model_type: str = Query("chat"), 
    system_prompt: Optional[str] = Query(None),
    temperature: Optional[float] = Query(0.7),
    user: UserInfo = Depends(get_current_user),
    session_id: Optional[str] = Query(None)
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="사용 권한이 없습니다.")

    actual_session_id = session_id or f"user_{user.sub}"
    
    if model_type == "reasoning":
        llm = LLMGateway.get_reasoning_llm(temperature=temperature)
    else:
        llm = LLMGateway.get_chat_llm(temperature=temperature)
    
    history = memory_manager.get_session_history(actual_session_id)
    
    async def event_generator():
        full_response = ""
        messages = [SystemMessage(content=system_prompt or "당신은 AI 어시스턴트입니다.")] + history.messages + [HumanMessage(content=message)]
        
        async for chunk in llm.astream(messages):
            content = chunk.content
            full_response += content
            yield f"data: {json.dumps({'content': content})}\n\n"
        
        history.add_user_message(message)
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

@app.get("/bulk")
async def bulk_page(request: Request):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url="/auth/login")
    return FileResponse(os.path.join(static_dir, "bulk.html"))

@app.post("/api/rag/bulk-upload")
async def start_bulk_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    collection: str = Form(...),
    domain_id: int = Form(...),
    user: UserInfo = Depends(get_current_user)
):
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

@app.get("/api/rag/bulk-progress/{task_id}")
async def get_bulk_progress(task_id: str):
    task = global_bulk_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.get("/api/rag/bulk-error-download/{task_id}")
async def download_bulk_errors(task_id: str):
    task = global_bulk_tasks.get(task_id)
    if not task or not task.get('error_file'):
        raise HTTPException(status_code=404, detail="Error file not found")
    return FileResponse(task['error_file'], filename=f"error_report_{task_id}.xlsx")

if __name__ == "__main__":
    uvicorn.run("apps.api.main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
