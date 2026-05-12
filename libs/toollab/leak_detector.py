"""Detect raw tool-call markup leaking into ``content``.

When a vLLM instance is started **without** ``--tool-call-parser``, the
model's native tool-call markup (e.g. ``<tool_call>``, ``<|tool▁call▁begin|>``,
or harmony channel tokens) ends up in ``message.content`` instead of being
parsed into ``tool_calls``. Our execution loop would then exit on the first
turn ("no tool calls"), making the failure look like the model *chose* not
to call any tool — which is indistinguishable from a healthy "no tool
needed" branch unless we look at the content explicitly.

This module gives us that signal so we can attach a ``warnings`` entry and
emit ``toollab_tool_parser_leak`` for ops alerting.

Reference: docs/P07_요구사항.md §4.3.3, docs/P07_설계서.md §6.2.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# (name, pattern). Names are stable identifiers — used in LLM_LOG label
# ``pattern_matched``. New patterns can be appended without renaming existing
# ones.
_LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("xml_tool_call", re.compile(r"<tool_call\b")),
    ("qwen_tool_call_begin", re.compile(r"<\|tool▁call▁begin\|>")),
    ("json_name_block", re.compile(r"```json\s*\{\s*\"name\"\s*:")),
    ("function_call_tag", re.compile(r"<function_call\b")),
    ("harmony_channel", re.compile(r"<\|channel\|>(commentary|analysis)\b")),
]


class LeakHit(NamedTuple):
    pattern: str
    excerpt: str  # ~200 chars around the match for ops triage


def detect_leak(content: str | None) -> LeakHit | None:
    """Scan ``content`` for known leak markers.

    Returns the first hit (with surrounding excerpt) or ``None``.
    """
    if not content:
        return None
    for name, rx in _LEAK_PATTERNS:
        m = rx.search(content)
        if m is None:
            continue
        start = max(0, m.start() - 20)
        end = min(len(content), m.end() + 200)
        return LeakHit(pattern=name, excerpt=content[start:end])
    return None
