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
import time

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
from libs.core.service import RAGService, PromptService
from libs.core.logging_helpers import emit_llm_log, extract_usage, rag_score_summary

# Phase 07 — Tool Lab. Importing the models here ensures they register on
# Base.metadata before startup's create_all runs.
from libs.toollab import models as _toollab_models  # noqa: F401 — side-effect import
from libs.toollab import seed as toollab_seed
from apps.api.routers.toollab import router as toollab_router

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
    # Phase 07 — hydrate the in-memory tool registry (seed + user tools).
    if settings.TOOLLAB_ENABLED:
        async with AsyncSessionLocal() as db:
            try:
                await toollab_seed.bootstrap(db)
            except Exception as exc:  # noqa: BLE001
                logger.exception("toollab bootstrap failed: %s", exc)


# Phase 07 — register Tool Lab API router (gated by TOOLLAB_ENABLED).
if settings.TOOLLAB_ENABLED:
    app.include_router(toollab_router)

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


@app.get("/prompts", tags=["UI"], summary="SPA - Prompt 관리 화면")
async def prompts_console(request: Request):
    """동일한 SPA 셸을 반환. 클라이언트 라우터가 Prompt 뷰를 활성화한다."""
    redirect = _require_user(request)
    if redirect:
        return redirect
    return FileResponse(SPA_INDEX_PATH)


@app.get("/toollab", tags=["UI"], summary="SPA - Tool Lab (편집)")
@app.get("/toollab/run", tags=["UI"], summary="SPA - Tool Run (실행)")
async def toollab_console(request: Request):
    """SPA 셸 — 클라이언트 라우터가 Tool Lab / Tool Run 뷰를 활성화.
    TOOLLAB_ALLOWED_GROUPS 멤버만 진입."""
    if not settings.TOOLLAB_ENABLED:
        return RedirectResponse(url="/static/unauthorized.html")
    redirect = _require_user(request)
    if redirect:
        return redirect
    user = request.session.get("user") or {}
    groups = set(user.get("groups", []))
    allowed = {
        g.strip()
        for g in (settings.TOOLLAB_ALLOWED_GROUPS or "").split(",")
        if g.strip()
    }
    if allowed and not (groups & allowed) and "Admin" not in groups:
        return RedirectResponse(url="/static/unauthorized.html")
    return FileResponse(SPA_INDEX_PATH)


from apps.api.api import api_router
app.include_router(api_router)

@app.get("/bulk", tags=["UI"], summary="SPA - 엑셀 일괄 업로드 화면")
async def bulk_page(request: Request):
    """동일한 SPA 셸을 반환. 클라이언트 라우터가 Bulk 뷰를 활성화한다."""
    redirect = _require_user(request)
    if redirect:
        return redirect
    return FileResponse(SPA_INDEX_PATH)

if __name__ == "__main__":
    uvicorn.run("apps.api.main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
