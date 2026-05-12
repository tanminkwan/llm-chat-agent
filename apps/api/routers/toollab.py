"""``/api/toollab/*`` — Phase 07 Tool Lab API.

Endpoints (per docs/P07_설계서.md §7.1, lightly trimmed):

* ``GET    /api/toollab/tools`` — list visible tools (own + system)
* ``POST   /api/toollab/tools`` — register or version-bump on (owner, name) collision
* ``PUT    /api/toollab/tools/{id}`` — edit existing row (version bump)
* ``PATCH  /api/toollab/tools/{id}/active`` — toggle is_active (no version bump)
* ``DELETE /api/toollab/tools/{id}`` — soft delete (is_active = False)
* ``POST   /api/toollab/tools/validate`` — dry-run AST/schema/signature checks
* ``POST   /api/toollab/run`` — execute prompt with bound tools, return trace
* ``GET    /api/toollab/runs`` — list runs (owner-scoped, Admin sees all)
* ``GET    /api/toollab/runs/{run_id}`` — full trace
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.core.auth import UserInfo, get_current_user
from libs.core.database import get_db
from libs.core.logging_helpers import emit_llm_log
from libs.core.settings import settings
from libs.toollab import sandbox
from libs.toollab.codegen import SchemaGenerationError, generate_schemas_from_code
from libs.toollab.models import ToolDefinition, ToolRun
from libs.toollab.registry import SYSTEM_OWNER, get_registry
from libs.toollab.runner import run as runner_run
from libs.toollab.schemas import (
    RunRequest,
    RunResult,
    RunStep,
    SchemaGenerationRequest,
    SchemaGenerationResult,
    ToolActiveToggle,
    ToolDefinitionInput,
    ToolDefinitionRead,
    ToolRunSummary,
    ToolValidationResult,
    ValidationError,
)

logger = logging.getLogger("llm-chat-agent.toollab.api")

router = APIRouter(prefix="/api/toollab", tags=["Tool Lab"])


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


def _allowed_groups() -> set[str]:
    return {
        g.strip()
        for g in (settings.TOOLLAB_ALLOWED_GROUPS or "").split(",")
        if g.strip()
    }


def require_toollab_access(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """Gate write/run endpoints on group membership.

    Read endpoints (GET /tools, GET /runs) only require auth so admins or
    other users can browse system tools, but mutating + executing must come
    from a member of ``TOOLLAB_ALLOWED_GROUPS``.
    """
    allowed = _allowed_groups()
    if not allowed:
        return user  # empty list disables the gate
    if user.is_admin or any(g in allowed for g in user.groups):
        return user
    raise HTTPException(
        status_code=403,
        detail=f"Tool Lab access requires one of groups: {sorted(allowed)}",
    )


def _orm_to_read(td: ToolDefinition, user: UserInfo) -> ToolDefinitionRead:
    return ToolDefinitionRead(
        id=td.id,
        owner_user_id=td.owner_user_id,
        name=td.name,
        version=td.version,
        description=td.description,
        parameters=td.parameters_json,
        returns=td.returns_json,
        code=td.code,
        tags=list(td.tags or []),
        is_active=td.is_active,
        is_public=bool(getattr(td, "is_public", False)),
        created_at=td.created_at,
        updated_at=td.updated_at,
        is_owner=(td.owner_user_id == user.sub),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_definition(payload: ToolDefinitionInput) -> ToolValidationResult:
    """Run all registration-time checks; never executes the handler."""
    errors: list[ValidationError] = []

    # 1. JSON Schema syntactic check on parameters / returns
    for fld_name, fld in (("parameters", payload.parameters),
                          ("returns", payload.returns)):
        msg = sandbox.validate_jsonschema(fld)
        if msg is not None:
            errors.append(ValidationError(
                kind="jsonschema", detail=f"{fld_name}: {msg}"
            ))

    if errors:
        return ToolValidationResult(ok=False, errors=errors)

    # 2. AST + compile + signature
    try:
        sandbox.compile_handler(payload.code, payload.parameters)
    except sandbox.ToolDefinitionError as e:
        errors.append(ValidationError(
            kind=e.kind, line=e.line, col=e.col, detail=e.detail
        ))

    return ToolValidationResult(ok=not errors, errors=errors)


# ---------------------------------------------------------------------------
# Endpoints — tool definitions
# ---------------------------------------------------------------------------


@router.get("/tools", response_model=list[ToolDefinitionRead],
            summary="도구 목록 조회 (내 것 + system, optional: 공유 공개)")
async def list_tools(
    include_inactive: bool = Query(False),
    include_shared: bool = Query(
        False,
        description="True 면 다른 사용자가 is_public=True 로 공개한 도구도 반환 "
                    "(편집 화면은 False, Run picker 는 True 로 호출).",
    ),
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(get_current_user),
) -> list[ToolDefinitionRead]:
    stmt = select(ToolDefinition)
    if user.is_admin:
        # Admin: everyone's tools.
        pass
    elif include_shared:
        # User: own + system + other users' is_public.
        stmt = stmt.where(
            (ToolDefinition.owner_user_id == user.sub)
            | (ToolDefinition.owner_user_id == SYSTEM_OWNER)
            | (ToolDefinition.is_public.is_(True))
        )
    else:
        # User: own + system.
        stmt = stmt.where(
            (ToolDefinition.owner_user_id == user.sub)
            | (ToolDefinition.owner_user_id == SYSTEM_OWNER)
        )
    if not include_inactive:
        stmt = stmt.where(ToolDefinition.is_active.is_(True))
    stmt = stmt.order_by(ToolDefinition.owner_user_id, ToolDefinition.name)
    rows = (await db.execute(stmt)).scalars().all()
    return [_orm_to_read(r, user) for r in rows]


@router.post("/tools/validate", response_model=ToolValidationResult,
             summary="등록 전 dry-run 검증")
async def validate_tool(
    payload: ToolDefinitionInput,
    user: UserInfo = Depends(require_toollab_access),
) -> ToolValidationResult:
    return _validate_definition(payload)


@router.post("/tools/generate-schemas", response_model=SchemaGenerationResult,
             summary="코드의 타입 힌트로 parameters/returns JSON Schema 자동 생성")
async def generate_schemas(
    payload: SchemaGenerationRequest,
    user: UserInfo = Depends(require_toollab_access),
) -> SchemaGenerationResult:
    """``handler`` 의 타입 힌트만 보고 schema 초안을 만든다 — 저장은 하지 않음."""
    try:
        gs = generate_schemas_from_code(payload.code)
    except SchemaGenerationError as e:
        return SchemaGenerationResult(
            ok=False,
            error=ValidationError(
                kind=e.kind, line=e.line, col=e.col, detail=e.detail,
            ),
        )
    return SchemaGenerationResult(
        ok=True,
        parameters=gs.parameters,
        returns=gs.returns,
        warnings=gs.warnings,
    )


@router.post("/tools", response_model=ToolDefinitionRead, status_code=201,
             summary="도구 등록 (이름 중복 시 자동 버전 증가)")
async def create_tool(
    payload: ToolDefinitionInput,
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(require_toollab_access),
) -> ToolDefinitionRead:
    return await _upsert_definition(payload, db, user, force_id=None)


@router.put("/tools/{tool_id}", response_model=ToolDefinitionRead,
            summary="도구 수정 (해당 row deactivate + 새 버전 INSERT)")
async def update_tool(
    tool_id: UUID,
    payload: ToolDefinitionInput,
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(require_toollab_access),
) -> ToolDefinitionRead:
    target = await _load_owned(tool_id, db, user)
    if target.name != payload.name:
        # Renames are essentially a different tool — disallow to keep the
        # version semantics clean. The user can DELETE then POST.
        raise HTTPException(
            status_code=400,
            detail="Renaming via PUT is not supported. Delete and re-create.",
        )
    return await _upsert_definition(payload, db, user, force_id=tool_id)


@router.patch("/tools/{tool_id}/active", response_model=ToolDefinitionRead,
              summary="활성/비활성 토글")
async def toggle_active(
    tool_id: UUID,
    body: ToolActiveToggle,
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(require_toollab_access),
) -> ToolDefinitionRead:
    td = await _load_owned(tool_id, db, user)
    td.is_active = body.is_active
    await db.commit()
    await db.refresh(td)
    registry = get_registry()
    if td.is_active:
        registry.register(td)
    else:
        registry.unregister(td.id)
    return _orm_to_read(td, user)


@router.delete("/tools/{tool_id}", status_code=204,
               summary="도구 소프트 삭제")
async def delete_tool(
    tool_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(require_toollab_access),
) -> Response:
    td = await _load_owned(tool_id, db, user)
    td.is_active = False
    await db.commit()
    get_registry().unregister(td.id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Endpoints — run / runs
# ---------------------------------------------------------------------------


@router.post("/run", response_model=RunResult, summary="자연어 입력 → 도구 호출 트레이스")
async def run_prompt(
    req: RunRequest,
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(require_toollab_access),
) -> RunResult:
    request_id = uuid.uuid4().hex
    return await runner_run(req, user, db, request_id)


@router.get("/runs", response_model=list[ToolRunSummary],
            summary="실행 이력 (cursor 페이지네이션)")
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[datetime] = Query(None, description="created_at(ISO) 미만 row 만 반환"),
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(get_current_user),
) -> list[ToolRunSummary]:
    stmt = select(ToolRun).order_by(desc(ToolRun.created_at))
    if not user.is_admin:
        stmt = stmt.where(ToolRun.owner_user_id == user.sub)
    if cursor is not None:
        stmt = stmt.where(ToolRun.created_at < cursor)
    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ToolRunSummary(
            run_id=r.id,
            prompt=r.prompt,
            model_type=r.model_type,
            model=r.model,
            iterations=r.iterations,
            truncated=r.truncated,
            latency_ms=r.latency_ms,
            warnings=list(r.warnings or []),
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/runs/{run_id}", response_model=RunResult,
            summary="단일 실행 트레이스 상세")
async def get_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: UserInfo = Depends(get_current_user),
) -> RunResult:
    row = (await db.execute(
        select(ToolRun).where(ToolRun.id == run_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not user.is_admin and row.owner_user_id != user.sub:
        raise HTTPException(status_code=403, detail="not yours")

    started = (
        row.created_at
        if row.latency_ms is None
        else (row.created_at if row.latency_ms == 0 else row.created_at)
    )
    # Reconstruct RunResult — started_at/ended_at aren't separately stored,
    # so we approximate from created_at + latency_ms.
    from datetime import timedelta
    ended = row.created_at
    started = (
        row.created_at - timedelta(milliseconds=row.latency_ms)
        if row.latency_ms else row.created_at
    )
    return RunResult(
        run_id=row.id,
        prompt=row.prompt,
        model_type=row.model_type,
        model=row.model,
        served_by=row.served_by,
        tool_call_parser=row.tool_call_parser,
        started_at=started,
        ended_at=ended,
        latency_ms=row.latency_ms or 0,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        iterations=row.iterations,
        truncated=row.truncated,
        warnings=list(row.warnings or []),
        steps=[RunStep(**s) for s in (row.trace_json or [])],
        final_response=row.final_response,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _load_owned(tool_id: UUID, db: AsyncSession,
                      user: UserInfo) -> ToolDefinition:
    td = (await db.execute(
        select(ToolDefinition).where(ToolDefinition.id == tool_id)
    )).scalar_one_or_none()
    if td is None:
        raise HTTPException(status_code=404, detail="tool not found")
    if td.owner_user_id == SYSTEM_OWNER and not user.is_admin:
        raise HTTPException(status_code=403, detail="system tool — admin only")
    if td.owner_user_id != user.sub and not user.is_admin:
        raise HTTPException(status_code=403, detail="not yours")
    return td


async def _upsert_definition(
    payload: ToolDefinitionInput,
    db: AsyncSession,
    user: UserInfo,
    force_id: Optional[UUID],
) -> ToolDefinitionRead:
    """Insert a new tool row (v1 if name is fresh, v(max+1) on collision)."""

    # Validate first — no DB write on failure.
    vr = _validate_definition(payload)
    if not vr.ok:
        raise HTTPException(status_code=400, detail={
            "errors": [e.model_dump() for e in vr.errors]
        })

    # Find current active row (if any) for this (owner, name).
    existing = (await db.execute(
        select(ToolDefinition)
        .where(ToolDefinition.owner_user_id == user.sub)
        .where(ToolDefinition.name == payload.name)
        .where(ToolDefinition.is_active.is_(True))
    )).scalar_one_or_none()

    next_version = 1
    previous_id: Optional[UUID] = None
    if existing is not None:
        previous_id = existing.id
        next_version = existing.version + 1
        existing.is_active = False  # deactivate; flush together below

    new_id = force_id or uuid.uuid4()
    td = ToolDefinition(
        id=new_id,
        owner_user_id=user.sub,
        name=payload.name,
        version=next_version,
        description=payload.description,
        parameters_json=payload.parameters,
        returns_json=payload.returns,
        code=payload.code,
        tags=list(payload.tags or []),
        is_active=payload.is_active,
        is_public=payload.is_public,
    )
    db.add(td)
    await db.commit()
    await db.refresh(td)

    # Compile + cache. Errors here would have been caught by _validate_definition,
    # but we wrap defensively so the DB row remains the source of truth.
    try:
        if td.is_active:
            get_registry().replace_active(td, previous_id=previous_id)
        elif previous_id is not None:
            get_registry().unregister(previous_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("registry update failed for %s: %s", td.id, e)

    emit_llm_log("debug", {
        "type": "toollab_tool_register",
        "user_id": user.sub,
        "tool_name": td.name,
        "tool_id": str(td.id),
        "version": td.version,
        "validated_ok": True,
    })
    return _orm_to_read(td, user)
