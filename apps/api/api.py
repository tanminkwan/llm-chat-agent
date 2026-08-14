from typing import Optional, List
from fastapi import APIRouter, Depends, Request, HTTPException, Query, Path, Body, BackgroundTasks, File, UploadFile, Form
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import httpx
import uuid
import time
import json
import logging
import pandas as pd
import io
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import HumanMessage, SystemMessage

from libs.core.settings import settings
from libs.core.llm import LLMGateway
from libs.core.memory import memory_manager
from libs.core.database import get_db, AsyncSessionLocal
from libs.core.service import RAGService, PromptService
from libs.core.logging_helpers import emit_llm_log, extract_usage, rag_score_summary

from .schemas import (
    CollectionCreate, CollectionRead, DomainCreate, DomainRead,
    KnowledgeCreate, SearchResult, SearchRequest, UserInfo, ConfigResponse,
    MessageResponse, TaskStatusResponse, DeleteCountResponse, ChatRequest,
    PromptCreate, PromptUpdate, PromptRead, ChatResponse,
    EmbeddingRequest, EmbeddingResponse
)

logger = logging.getLogger("llm-chat-agent")

api_router = APIRouter()

bearer_scheme = HTTPBearer(auto_error=False)

async def get_current_user(
    request: Request, 
    token: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> UserInfo:
    """세션 또는 API Key(Bearer)에서 사용자 정보를 가져오는 의존성 주입 함수"""
    
    if settings.NON_LOGIN_SERVICE:
        return UserInfo(
            sub="nobody",
            preferred_username="nobody",
            groups=["Admin"]
        )

    # 1. API Key 검증 (Proxy to IDP)
    if token and token.credentials:
        api_key = token.credentials
        try:
            async with httpx.AsyncClient(verify=False) as client:
                idp_url = f"{settings.OIDC_ISSUER}/api/sync/status"
                resp = await client.get(
                    idp_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=3.0
                )
                if resp.status_code == 200:
                    return UserInfo(
                        sub="api-key-user",
                        preferred_username="API Client",
                        groups=["Admin"],
                        email="api@client.local"
                    )
        except Exception as e:
            logger.error(f"API Key validation failed: {e}")

    # 2. 세션 검증 (기존 방식 Fallback)
    user = request.session.get('user')
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserInfo(**user)

@api_router.get("/user/me", tags=["System"], response_model=UserInfo, summary="현재 사용자 정보 조회")
async def get_me(user: UserInfo = Depends(get_current_user)):
    """현재 로그인된 사용자의 ID, 이름, 권한(그룹) 정보를 반환합니다."""
    return user

@api_router.get("/api/config", tags=["System"], response_model=ConfigResponse, summary="UI 설정 정보 조회")
async def get_config():
    """서버의 앱 이름, LLM 모델 설정 등 프론트엔드 렌더링에 필요한 환경 변수를 반환합니다."""
    return {
        "app_name": settings.APP_NAME,
        "chat_model": settings.CHAT_LLM_MODEL,
        "chat_label": settings.CHAT_LLM_LABEL,
        "reasoning_model": settings.REASONING_LLM_MODEL,
        "reasoning_label": settings.REASONING_LLM_LABEL,
        "grafana_url": settings.GRAFANA_ROOT_URL,
        "toollab_enabled": settings.TOOLLAB_ENABLED,
        "toollab_allowed_groups": [
            g.strip()
            for g in (settings.TOOLLAB_ALLOWED_GROUPS or "").split(",")
            if g.strip()
        ],
    }

# --- RAG 관리 엔드포인트 (User 허용 기능) ---

def get_rag_service(db: AsyncSession = Depends(get_db)) -> RAGService:
    return RAGService(db)

@api_router.post("/api/collections", tags=["RAG Management"], response_model=CollectionRead, summary="콜렉션 생성")
async def create_collection(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user),
    data: CollectionCreate = Body(...)
):
    """새로운 지식 콜렉션(벡터 공간)을 생성합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.create_collection(**data.model_dump())

@api_router.get("/api/collections", tags=["RAG Management"], response_model=List[CollectionRead], summary="콜렉션 목록 조회")
async def list_collections(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """현재 시스템에 등록된 모든 콜렉션 정보를 가져옵니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.list_collections()

@api_router.put("/api/collections/{collection_name}", tags=["RAG Management"], response_model=CollectionRead, summary="콜렉션 정보 수정")
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

@api_router.delete("/api/collections/{collection_name}", tags=["RAG Management"], response_model=MessageResponse, summary="콜렉션 삭제")
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

@api_router.post("/api/domains", tags=["RAG Management"], response_model=DomainRead, summary="도메인(분류) 생성")
async def create_domain(
    data: DomainCreate,
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """지식을 그룹화할 도메인(분류)을 생성합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.create_domain(name=data.name)

@api_router.get("/api/domains", tags=["RAG Management"], response_model=List[DomainRead], summary="도메인 목록 조회")
async def list_domains(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """현재 등록된 모든 도메인 목록을 가져옵니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    return await service.list_domains()

@api_router.put("/api/domains/{dom_id}", tags=["RAG Management"], response_model=DomainRead, summary="도메인 정보 수정")
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

@api_router.delete("/api/domains/{dom_id}", tags=["RAG Management"], response_model=MessageResponse, summary="도메인 삭제")
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

@api_router.post("/api/rag/search", tags=["RAG Data"], response_model=List[SearchResult], summary="통합 RAG 검색 (JSON Body)")
async def search_rag(
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user),
    request: SearchRequest = Body(...)
):
    """지정된 콜렉션에서 쿼리와 가장 유사한 지식 조각들을 검색하여 점수 순으로 반환합니다."""
    request_id = str(uuid.uuid4())[:8]
    user_id = user.sub
    started_at = time.perf_counter()

    emit_llm_log("debug", {
        "request_id": request_id,
        "user_id": user_id,
        "type": "rag_search_request",
        "collection_id": request.collection_id,
        "domain_id": request.domain_id,
        "query": request.query,
        "search_method": request.search_method,
    })

    try:
        results = await service.search_rag(
            collection_id=request.collection_id,
            domain_id=request.domain_id,
            query=request.query,
            search_method=request.search_method,
            limit=request.limit
        )
    except Exception as e:
        emit_llm_log("error", {
            "request_id": request_id,
            "user_id": user_id,
            "type": "rag_search_error",
            "search_method": request.search_method,
            "collection_id": request.collection_id,
            "domain_id": request.domain_id,
            "error": str(e),
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
        })
        raise

    score_summary = rag_score_summary(results)
    emit_llm_log("debug", {
        "request_id": request_id,
        "user_id": user_id,
        "type": "rag_search_response",
        "search_method": request.search_method,
        "results_count": len(results),
        "top_score": score_summary["top_score"],
        "min_score": score_summary["min_score"],
        "latency_ms": int((time.perf_counter() - started_at) * 1000),
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
    })

    return results

@api_router.post("/api/rag/knowledge", tags=["RAG Data"], summary="개별 지식 등록/수정")
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

@api_router.delete("/api/rag/knowledge/{collection_name}/{point_id}", tags=["RAG Data"], response_model=MessageResponse, summary="개별 지식 삭제")
async def delete_knowledge(
    collection_name: str = Path(..., description="데이터가 속한 콜렉션 ID"),
    point_id: str = Path(..., description="삭제할 데이터 고유 ID"),
    service: RAGService = Depends(get_rag_service),
    user: UserInfo = Depends(get_current_user)
):
    """콜렉션 내의 특정 지식 데이터(포인트) 하나를 삭제합니다."""
    return await service.delete_knowledge_point(collection_name, point_id)

@api_router.get("/api/rag/delete-count", tags=["RAG Data"], response_model=DeleteCountResponse, summary="삭제 대상 건수 확인")
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

@api_router.delete("/api/rag/bulk-delete", tags=["RAG Data"], response_model=MessageResponse, summary="조건부 일괄 삭제")
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

# --- Prompt 관리 엔드포인트 (User 권한) ---

def get_prompt_service(db: AsyncSession = Depends(get_db)) -> PromptService:
    return PromptService(db)

def _to_prompt_read(prompt, current_user_id: str) -> dict:
    return {
        "id": prompt.id,
        "user_id": prompt.user_id,
        "username": prompt.username,
        "title": prompt.title,
        "content": prompt.content,
        "is_public": prompt.is_public,
        "is_owner": prompt.user_id == current_user_id,
        "created_at": prompt.created_at,
        "updated_at": prompt.updated_at,
    }

@api_router.get("/api/prompts", tags=["Prompt"], response_model=List[PromptRead], summary="Prompt 목록 조회")
async def list_prompts(
    include_others: bool = Query(False, description="True 이면 타 user 가 공유한 Prompt 까지 포함"),
    title: Optional[str] = Query(None, description="제목 부분 일치 검색어"),
    service: PromptService = Depends(get_prompt_service),
    user: UserInfo = Depends(get_current_user),
):
    """본인 소유 Prompt 와 (옵션) 타 user 가 공개한 Prompt 목록을 조회합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    prompts = await service.list_prompts(
        user_id=user.sub,
        include_others=include_others,
        title_keyword=(title.strip() if title else None) or None,
    )
    return [_to_prompt_read(p, user.sub) for p in prompts]

@api_router.get("/api/prompts/{prompt_id}", tags=["Prompt"], response_model=PromptRead, summary="Prompt 단건 조회")
async def get_prompt(
    prompt_id: int = Path(..., description="조회할 Prompt 고유 번호"),
    service: PromptService = Depends(get_prompt_service),
    user: UserInfo = Depends(get_current_user),
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        prompt = await service.get_prompt(prompt_id, user.sub)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return _to_prompt_read(prompt, user.sub)

@api_router.post("/api/prompts", tags=["Prompt"], response_model=PromptRead, summary="Prompt 신규 등록")
async def create_prompt(
    data: PromptCreate = Body(...),
    service: PromptService = Depends(get_prompt_service),
    user: UserInfo = Depends(get_current_user),
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    prompt = await service.create_prompt(
        user_id=user.sub,
        username=user.preferred_username,
        title=data.title,
        content=data.content,
        is_public=data.is_public,
    )
    return _to_prompt_read(prompt, user.sub)

@api_router.put("/api/prompts/{prompt_id}", tags=["Prompt"], response_model=PromptRead, summary="Prompt 수정")
async def update_prompt(
    prompt_id: int = Path(..., description="수정할 Prompt 고유 번호"),
    data: PromptUpdate = Body(...),
    service: PromptService = Depends(get_prompt_service),
    user: UserInfo = Depends(get_current_user),
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        prompt = await service.update_prompt(
            prompt_id=prompt_id,
            user_id=user.sub,
            title=data.title,
            content=data.content,
            is_public=data.is_public,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return _to_prompt_read(prompt, user.sub)

@api_router.delete("/api/prompts/{prompt_id}", tags=["Prompt"], response_model=MessageResponse, summary="Prompt 삭제")
async def delete_prompt(
    prompt_id: int = Path(..., description="삭제할 Prompt 고유 번호"),
    service: PromptService = Depends(get_prompt_service),
    user: UserInfo = Depends(get_current_user),
):
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    try:
        await service.delete_prompt(prompt_id, user.sub)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"message": "Prompt deleted successfully"}

# --- 채팅 엔드포인트 ---

@api_router.post("/chat", tags=["Chat"], summary="LLM 대화 (Streaming)")
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
        final_chunk = None
        request_id = str(uuid.uuid4())[:8] # 고유 요청 ID (Trace ID)
        user_id = user.sub
        started_at = time.perf_counter()
        system_prompt = request.system_prompt or "당신은 AI 어시스턴트입니다."
        messages = [SystemMessage(content=system_prompt)] + history.messages + [HumanMessage(content=request.message)]

        emit_llm_log("debug", {
            "request_id": request_id,
            "user_id": user_id,
            "type": "request",
            "thread_id": actual_thread_id,
            "model_type": request.model_type,
            "messages": [{"role": msg.type, "content": msg.content} for msg in messages],
        })

        async def stream_with_logging():
            nonlocal full_response, final_chunk
            try:
                aggregated_chunk = None
                async for chunk in llm.astream(messages):
                    if aggregated_chunk is None:
                        aggregated_chunk = chunk
                    else:
                        aggregated_chunk += chunk
                        
                    content = chunk.content
                    if content:
                        full_response += content
                        yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                final_chunk = aggregated_chunk
            except Exception as e:
                emit_llm_log("error", {
                    "request_id": request_id,
                    "thread_id": actual_thread_id,
                    "user_id": user_id,
                    "type": "error",
                    "error": str(e),
                    "latency_ms": int((time.perf_counter() - started_at) * 1000),
                })
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        async for event in stream_with_logging():
            yield event

        usage = extract_usage(final_chunk)
        emit_llm_log("debug", {
            "request_id": request_id,
            "thread_id": actual_thread_id,
            "user_id": user_id,
            "type": "response",
            "model_type": request.model_type,
            "model": usage["model"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
            "full_response": full_response,
        })

        history.add_user_message(request.message)
        history.add_ai_message(full_response)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@api_router.post("/api/chat/sync", tags=["Chat"], response_model=ChatResponse, summary="LLM 대화 (Single Response)")
async def chat_sync(
    user: UserInfo = Depends(get_current_user),
    request: ChatRequest = Body(...)
):
    """LLM과 대화를 수행하며, 스트리밍 없이 완성된 최종 응답을 JSON 형태로 반환합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="사용 권한이 없습니다.")

    actual_thread_id = request.thread_id or f"user_{user.sub}"
    
    if request.model_type == "reasoning":
        llm = LLMGateway.get_reasoning_llm(streaming=False, temperature=request.temperature)
    else:
        llm = LLMGateway.get_chat_llm(streaming=False, temperature=request.temperature)
    
    history = memory_manager.get_thread_history(actual_thread_id)
    
    request_id = str(uuid.uuid4())[:8]
    user_id = user.sub
    started_at = time.perf_counter()
    system_prompt = request.system_prompt or "당신은 AI 어시스턴트입니다."
    messages = [SystemMessage(content=system_prompt)] + history.messages + [HumanMessage(content=request.message)]

    emit_llm_log("debug", {
        "request_id": request_id,
        "user_id": user_id,
        "type": "request",
        "thread_id": actual_thread_id,
        "model_type": request.model_type,
        "messages": [{"role": msg.type, "content": msg.content} for msg in messages],
    })

    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        emit_llm_log("error", {
            "request_id": request_id,
            "thread_id": actual_thread_id,
            "user_id": user_id,
            "type": "error",
            "error": str(e),
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
        })
        raise HTTPException(status_code=500, detail=str(e))

    full_response = str(response.content)
    usage = extract_usage(response)

    emit_llm_log("debug", {
        "request_id": request_id,
        "thread_id": actual_thread_id,
        "user_id": user_id,
        "type": "response",
        "model_type": request.model_type,
        "model": usage["model"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "latency_ms": int((time.perf_counter() - started_at) * 1000),
        "full_response": full_response,
    })

    history.add_user_message(request.message)
    history.add_ai_message(full_response)
    
    return ChatResponse(content=full_response, usage=usage)

@api_router.post("/api/embeddings", tags=["LLM"], response_model=EmbeddingResponse, summary="텍스트 임베딩 추출")
async def get_embeddings_api(
    user: UserInfo = Depends(get_current_user),
    request: EmbeddingRequest = Body(...)
):
    """여러 텍스트를 입력받아 각각의 임베딩 벡터를 반환합니다."""
    if not any(role in user.groups for role in ["Admin", "User"]):
        raise HTTPException(status_code=403, detail="사용 권한이 없습니다.")

    try:
        embedder = LLMGateway.get_embeddings()
        if hasattr(embedder, "aembed_documents"):
            embeddings = await embedder.aembed_documents(request.texts)
        else:
            embeddings = embedder.embed_documents(request.texts)
        return EmbeddingResponse(embeddings=embeddings)
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

@api_router.post("/api/rag/bulk-upload", tags=["Bulk Operations"], summary="엑셀 일괄 업로드 시작")
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

@api_router.get("/api/rag/bulk-progress/{task_id}", tags=["Bulk Operations"], response_model=TaskStatusResponse, summary="일괄 업로드 진행 상태 조회")
async def get_bulk_progress(task_id: str = Path(..., description="업로드 시작 시 발급받은 작업 ID")):
    """백그라운드에서 실행 중인 엑셀 업로드 작업의 진행 건수와 성공/실패 여부를 확인합니다."""
    task = global_bulk_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@api_router.get("/api/rag/bulk-error-download/{task_id}", tags=["Bulk Operations"], summary="업로드 실패 내역 다운로드")
async def download_bulk_errors(task_id: str = Path(..., description="작업 ID")):
    """업로드 과정에서 발생한 실패 데이터와 사유가 적힌 엑셀 파일을 다운로드합니다."""
    task = global_bulk_tasks.get(task_id)
    if not task or not task.get('error_file'):
        raise HTTPException(status_code=404, detail="Error file not found")
    return FileResponse(task['error_file'], filename=f"error_report_{task_id}.xlsx")

