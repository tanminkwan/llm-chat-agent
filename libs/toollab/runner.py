"""Tool Lab execution runner — bind_tools loop + trace assembly + LLM_LOG.

The runner is the only place where:

* the LLM is invoked with bound tools,
* the registry is asked to execute a handler,
* :func:`leak_detector.detect_leak` is consulted to flag a parser-misconfig,
* the in-flight :class:`~libs.toollab.schemas.RunResult` is assembled, and
* ``[LLM_LOG]`` lines for ``toollab_*`` are emitted.

Reference: docs/P07_설계서.md §5, §6.
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any, Optional
from uuid import UUID, uuid4

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy.ext.asyncio import AsyncSession

from libs.core.auth import UserInfo
from libs.core.llm import LLMGateway, extract_reasoning, get_llm_meta
from libs.core.logging_helpers import emit_llm_log, extract_usage, now_iso
from libs.core.settings import settings
from libs.toollab.leak_detector import detect_leak
from libs.toollab.models import ToolRun
from libs.toollab.registry import get_registry
from libs.toollab.schemas import RunRequest, RunResult, RunStep
from libs.toollab.serializer import to_tool_message_content

logger = logging.getLogger("llm-chat-agent.toollab.runner")

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant that can call tools to fulfil the user's "
    "request. Work iteratively: call one tool, examine its result, then "
    "decide whether another tool call is needed. If a later call depends on "
    "an earlier result (e.g. 'create only if not found'), do NOT bundle them "
    "in one turn — wait for the previous result first. Prefer calling a tool "
    "when its description matches the task; answer directly when no tool is "
    "appropriate."
)


# ---------------------------------------------------------------------------
# Trace assembly
# ---------------------------------------------------------------------------


class _TraceAssembler:
    """Mutable scratch-pad that becomes a :class:`RunResult` at the end."""

    def __init__(self, req: RunRequest, run_id: UUID, request_id: str,
                 user: UserInfo, started: float):
        self.req = req
        self.run_id = run_id
        self.request_id = request_id
        self.user = user
        self.started_monotonic = started
        self.started_at = _dt.datetime.now(_dt.timezone.utc)
        self.steps: list[RunStep] = []
        self.warnings: list[str] = []
        self.input_tokens: Optional[int] = None
        self.output_tokens: Optional[int] = None
        self.last_ai_content: Optional[str] = None
        self._step_counter = 0

    def _next_step(self) -> int:
        self._step_counter += 1
        return self._step_counter

    def record_ai(self, ai: Any) -> None:
        reasoning = extract_reasoning(ai)
        tool_calls = []
        for tc in (getattr(ai, "tool_calls", None) or []):
            # LangChain normalises tool_calls to {id, name, args, type}
            tool_calls.append({
                "id": tc.get("id"),
                "name": tc.get("name"),
                "args": tc.get("args") or {},
            })
        content = getattr(ai, "content", None)
        if isinstance(content, str):
            self.last_ai_content = content
        self.steps.append(RunStep(
            step=self._next_step(),
            kind="ai",
            content=content if isinstance(content, str) else None,
            reasoning=reasoning,
            tool_calls=tool_calls or None,
        ))
        usage = extract_usage(ai)
        if usage["input_tokens"] is not None:
            self.input_tokens = (self.input_tokens or 0) + usage["input_tokens"]
        if usage["output_tokens"] is not None:
            self.output_tokens = (self.output_tokens or 0) + usage["output_tokens"]

    def record_tool(self, call: dict, exec_result: dict) -> None:
        self.steps.append(RunStep(
            step=self._next_step(),
            kind="tool",
            tool_call_id=call.get("id"),
            name=call.get("name"),
            args=call.get("args") or {},
            ok=exec_result.get("ok"),
            result=exec_result.get("result") if exec_result.get("ok") else None,
            error=exec_result.get("error") if not exec_result.get("ok") else None,
            latency_ms=exec_result.get("latency_ms"),
        ))

    def add_warning(self, key: str) -> None:
        if key not in self.warnings:
            self.warnings.append(key)

    def finalize(self, *, iterations: int, truncated: bool,
                 final_response: Optional[str]) -> RunResult:
        ended = _dt.datetime.now(_dt.timezone.utc)
        meta = get_llm_meta(self.req.model_type)
        return RunResult(
            run_id=self.run_id,
            prompt=self.req.prompt,
            model_type=self.req.model_type,
            model=meta.model_id,
            served_by=meta.served_by,
            tool_call_parser=meta.tool_call_parser,
            started_at=self.started_at,
            ended_at=ended,
            latency_ms=int((time.monotonic() - self.started_monotonic) * 1000),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            iterations=iterations,
            truncated=truncated,
            warnings=list(self.warnings),
            steps=list(self.steps),
            final_response=final_response,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run(req: RunRequest, user: UserInfo, db: AsyncSession,
              request_id: str) -> RunResult:
    """Execute a single Tool Lab run end-to-end and persist the trace."""

    run_id = uuid4()
    started = time.monotonic()
    trace = _TraceAssembler(req, run_id, request_id, user, started)
    meta = get_llm_meta(req.model_type)

    # Common LLM_LOG labels — repeated on every line so Loki/Grafana queries
    # can group/filter without joining.
    common_labels = {
        "model_id": meta.model_id,
        "served_by": meta.served_by,
        "tool_call_parser": meta.tool_call_parser,
    }

    sysprompt = req.system_prompt or DEFAULT_SYSTEM_PROMPT

    history_dump = (
        [m.model_dump() for m in req.history] if req.history else []
    )

    # --- request line --------------------------------------------------
    emit_llm_log("debug", {
        "type": "toollab_run_request",
        "request_id": request_id,
        "user_id": user.sub,
        "run_id": str(run_id),
        "model_type": req.model_type,
        "tool_ids": [str(t) for t in (req.tool_ids or [])],
        "system_prompt": sysprompt,
        "prompt": req.prompt,
        "history": history_dump,
        "history_length": len(history_dump),
        **common_labels,
    })

    # --- prepare LLM + tools ------------------------------------------
    registry = get_registry()
    visible = registry.list_for_user(user.sub, req.tool_ids)
    tool_dicts = [t.tool_dict for t in visible]

    if req.model_type == "chat":
        llm = LLMGateway.get_chat_llm(streaming=False, temperature=0.0)
    elif req.model_type == "reasoning":
        llm = LLMGateway.get_reasoning_llm(streaming=False)
    else:
        raise ValueError(f"unknown model_type {req.model_type!r}")

    if tool_dicts:
        llm = llm.bind_tools(tool_dicts, tool_choice="auto",
                             parallel_tool_calls=req.parallel_tool_calls)

    # --- conversation seed --------------------------------------------
    messages: list[Any] = []
    messages.append(SystemMessage(content=sysprompt))
    for m in (req.history or []):
        if m.role == "user":
            messages.append(HumanMessage(content=m.content or ""))
        elif m.role == "assistant":
            tcs = []
            for tc in (m.tool_calls or []):
                tcs.append({
                    "id": tc.get("id") or "",
                    "name": tc.get("name") or "",
                    "args": tc.get("args") or {},
                    "type": "tool_call",
                })
            messages.append(AIMessage(content=m.content or "", tool_calls=tcs))
        elif m.role == "tool":
            messages.append(ToolMessage(
                content=m.content or "",
                tool_call_id=m.tool_call_id or "",
                name=m.name or "",
            ))
    messages.append(HumanMessage(content=req.prompt))

    # --- iteration loop ------------------------------------------------
    max_iter = min(
        req.max_tool_iterations or settings.TOOLLAB_MAX_TOOL_ITERATIONS,
        settings.TOOLLAB_MAX_TOOL_ITERATIONS_HARD,
    )
    iterations = 0
    truncated = False
    final_response: Optional[str] = None

    while True:
        if iterations >= max_iter:
            truncated = True
            break
        iterations += 1

        try:
            ai = await llm.ainvoke(messages)
        except Exception as e:  # noqa: BLE001 — LLM failures are recorded
            logger.exception("LLM invoke failed: %s", e)
            trace.add_warning("llm_invoke_failed")
            emit_llm_log("error", {
                "type": "toollab_run_error",
                "request_id": request_id,
                "run_id": str(run_id),
                "iteration": iterations,
                "error_kind": type(e).__name__,
                "error": str(e)[:500],
                **common_labels,
            })
            break

        trace.record_ai(ai)
        messages.append(ai)

        ai_step = trace.steps[-1]
        emit_llm_log("debug", {
            "type": "toollab_ai_step",
            "request_id": request_id,
            "user_id": user.sub,
            "run_id": str(run_id),
            "iteration": iterations,
            "content": ai_step.content,
            "reasoning": ai_step.reasoning,
            "tool_calls": ai_step.tool_calls,
            **common_labels,
        })

        tool_calls = list(getattr(ai, "tool_calls", None) or [])
        if not tool_calls:
            content = getattr(ai, "content", "") or ""
            leak = detect_leak(content if isinstance(content, str) else "")
            if leak is not None:
                trace.add_warning("tool_call_parser_likely_missing")
                emit_llm_log("error", {
                    "type": "toollab_tool_parser_leak",
                    "request_id": request_id,
                    "run_id": str(run_id),
                    "pattern_matched": leak.pattern,
                    "content_excerpt": leak.excerpt[:200],
                    **common_labels,
                })
            final_response = content if isinstance(content, str) else None
            break

        # Run each tool call; capture results back into the conversation.
        for call in tool_calls:
            exec_result = registry.execute(
                tool_name=call.get("name", ""),
                args=call.get("args") or {},
                user_sub=user.sub,
            )
            trace.record_tool(call, exec_result)
            content_str = to_tool_message_content(exec_result)
            messages.append(ToolMessage(
                content=content_str,
                tool_call_id=call.get("id") or "",
                name=call.get("name") or "",
            ))
            emit_llm_log("debug", {
                "type": "toollab_tool_call",
                "request_id": request_id,
                "user_id": user.sub,
                "run_id": str(run_id),
                "tool_name": call.get("name"),
                "tool_call_id": call.get("id"),
                "args": call.get("args") or {},
                "result": exec_result.get("result") if exec_result.get("ok") else None,
                "error": exec_result.get("error") if not exec_result.get("ok") else None,
                "latency_ms": exec_result.get("latency_ms"),
                "ok": exec_result.get("ok"),
                "error_kind": exec_result.get("error_kind"),
                **common_labels,
            })

    # If we exited the loop with truncation, the last AI's content (if any)
    # becomes the final response; final_response stays None if no AI produced
    # plain content.
    if truncated and final_response is None:
        final_response = trace.last_ai_content

    result = trace.finalize(
        iterations=iterations,
        truncated=truncated,
        final_response=final_response,
    )

    # --- response line + persistence ----------------------------------
    emit_llm_log("debug", {
        "type": "toollab_run_response",
        "request_id": request_id,
        "user_id": user.sub,
        "run_id": str(run_id),
        "iterations": iterations,
        "truncated": truncated,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "final_response": result.final_response,
        "final_response_length": len(result.final_response or ""),
        "warnings": list(result.warnings),
        **common_labels,
    })

    db.add(ToolRun(
        id=run_id,
        owner_user_id=user.sub,
        model_type=req.model_type,
        model=meta.model_id,
        served_by=meta.served_by,
        tool_call_parser=meta.tool_call_parser,
        prompt=req.prompt,
        system_prompt=req.system_prompt,
        tool_ids=[t.tool_id for t in visible],
        iterations=iterations,
        truncated=truncated,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        trace_json=result.model_dump(mode="json")["steps"],
        final_response=result.final_response,
        warnings=list(result.warnings),
    ))
    await db.commit()

    return result
