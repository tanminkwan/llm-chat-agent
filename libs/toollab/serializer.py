"""ToolMessage content serializer.

The chat-template / tool-call-parser combos behind some vLLM builds reject
``role=tool`` messages whose ``content`` is a stringified JSON **array**
(top-level ``[...]``), returning HTTP 400. We learnt this the hard way during
P07 validation (T5c, see docs/P07_vllm_tool_calling_검증.md §T5c "부수 발견").

Invariant enforced here:

    *Every* ``content`` string we ship to the LLM begins with ``{`` —
    i.e. its top-level JSON is an *object*, never a bare array.

This is the only place that bytes-level decision is made; downstream callers
are blind to the wrapping. If a future model rejects even object-wrapped
JSON, swap the body of :func:`to_tool_message_content` to fall back to a
natural-language summary; no other module needs to change.
"""
from __future__ import annotations

import json
from typing import Any


def to_tool_message_content(exec_result: dict) -> str:
    """Convert the sandbox's exec_result dict into a tool message content string.

    ``exec_result`` shape (from :func:`sandbox.run_handler_with_timeout`)::

        {"ok": True,  "result": <handler return>, "latency_ms": int} |
        {"ok": False, "error_kind": str, "error": str, "latency_ms": int}
    """
    if not exec_result.get("ok", False):
        # Errors are always object-shaped already.
        return json.dumps(
            {
                "ok": False,
                "error_kind": exec_result.get("error_kind"),
                "error": exec_result.get("error"),
            },
            ensure_ascii=False,
        )

    result = exec_result.get("result")
    # Wrap *every* non-error result in an object. Even scalars/lists go
    # through the wrap so the top-level char of the serialised content
    # is always '{' — see module docstring.
    return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
