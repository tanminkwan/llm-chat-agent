"""Pydantic I/O models for Tool Lab API.

Reference: docs/P07_설계서.md §7.4.
"""
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------


class ToolDefinitionInput(BaseModel):
    """Body of POST/PUT /api/toollab/tools."""

    name: str = Field(
        ...,
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$",
        description="LLM 에 노출되는 함수명",
    )
    description: str = Field(..., min_length=1)
    parameters: dict = Field(
        ..., description="OpenAI tool spec parameters (JSON Schema Draft 2020-12)"
    )
    returns: dict = Field(
        ..., description="반환값 JSON Schema (실행 후 검증·UI 표기용)"
    )
    code: str = Field(..., min_length=1, description="Python 본문 (handler 포함)")
    tags: list[str] = Field(default_factory=list)
    is_active: bool = True
    is_public: bool = Field(
        False,
        description="True 면 다른 사용자에게도 Run picker 에서 공개. 편집/삭제 권한은 소유자만.",
    )


class ToolDefinitionRead(ToolDefinitionInput):
    id: UUID
    owner_user_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    is_owner: bool = Field(
        False, description="현재 사용자가 이 도구의 소유자인지 여부"
    )

    model_config = ConfigDict(from_attributes=True)


class ToolActiveToggle(BaseModel):
    is_active: bool


class ValidationError(BaseModel):
    kind: str = Field(..., description="schema | ast | signature | jsonschema")
    line: Optional[int] = None
    col: Optional[int] = None
    detail: str


class ToolValidationResult(BaseModel):
    ok: bool
    errors: list[ValidationError] = Field(default_factory=list)


class SchemaGenerationRequest(BaseModel):
    """Body of POST /api/toollab/tools/generate-schemas."""

    code: str = Field(..., min_length=1, description="`def handler(...)` 포함 Python 코드")


class SchemaGenerationResult(BaseModel):
    """타입 힌트 기반 schema 자동 생성 결과.

    ``ok=False`` 일 때만 ``error`` 가 채워지고 schema 는 비어있다. UI 는 결과를
    텍스트박스에 미리 채워 사용자가 검토 후 저장하도록 한다.
    """

    ok: bool
    parameters: Optional[dict] = None
    returns: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)
    error: Optional[ValidationError] = None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class RunHistoryMessage(BaseModel):
    """이전 turn 의 메시지 1건 (프론트 메모리에서 복원)."""

    role: Literal["user", "assistant", "tool"]
    content: Optional[str] = None
    # assistant 전용 (있을 수도, 없을 수도)
    tool_calls: Optional[list[dict]] = None
    # tool 전용
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class RunRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model_type: Literal["chat", "reasoning"]
    system_prompt: Optional[str] = None
    tool_ids: Optional[list[UUID]] = None
    max_tool_iterations: Optional[int] = Field(None, ge=1, le=16)
    # 조건부 흐름 (예: get_user 가 null 이면 create_user) 을 안정화하려면
    # 한 턴에 한 tool 만 부르도록 강제하는 게 안전하다 → default False.
    # True 로 두려면 vLLM 쪽 parser 가 multi-tool 을 지원해야 효과가 난다.
    parallel_tool_calls: bool = Field(
        False,
        description="True 면 한 AI 메시지에 여러 tool_call 을 묶을 수 있음 (parallel). "
                    "default False — sequential.",
    )
    history: Optional[list[RunHistoryMessage]] = Field(
        None,
        description="직전 turn 들의 메시지 — 프론트가 메모리에 들고 있다가 다음 호출에 동봉. "
                    "None/빈 배열이면 새 대화. 서버는 persistence 하지 않음.",
    )


class RunStep(BaseModel):
    step: int
    kind: Literal["ai", "tool"]

    # ai-only
    content: Optional[str] = None
    reasoning: Optional[str] = None
    tool_calls: Optional[list[dict]] = None

    # tool-only
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    args: Optional[dict] = None
    ok: Optional[bool] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    latency_ms: Optional[int] = None


class RunResult(BaseModel):
    run_id: UUID
    prompt: str
    model_type: Literal["chat", "reasoning"]
    model: str
    served_by: Literal["openai", "vllm"]
    tool_call_parser: Optional[str] = None
    started_at: datetime
    ended_at: datetime
    latency_ms: int
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    iterations: int
    truncated: bool
    warnings: list[str] = Field(default_factory=list)
    steps: list[RunStep]
    final_response: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ToolRunSummary(BaseModel):
    """List item for GET /api/toollab/runs."""

    run_id: UUID
    prompt: str
    model_type: Literal["chat", "reasoning"]
    model: str
    iterations: int
    truncated: bool
    latency_ms: Optional[int] = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
