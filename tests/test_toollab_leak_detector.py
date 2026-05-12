"""Tests for ``libs.toollab.leak_detector``.

We validate (a) every documented pattern triggers, and (b) ordinary natural-
language responses don't false-positive — false-positive 0 is the thing that
makes the warning useful in production.
"""
from __future__ import annotations

import pytest

from libs.toollab.leak_detector import detect_leak


class TestPatternHits:
    @pytest.mark.parametrize("content,expected_pattern", [
        ("Sure, calling: <tool_call>{name:'add'}</tool_call>", "xml_tool_call"),
        ("Now I will <|tool▁call▁begin|>add(7,13)<|tool▁call▁end|>", "qwen_tool_call_begin"),
        ('Here we go: ```json\n{"name": "add", "arguments": {"a": 7}}\n```',
         "json_name_block"),
        ("OK <function_call name=\"add\">{a:7}</function_call>",
         "function_call_tag"),
        ("<|channel|>analysis<|message|>I should add 7 and 13.",
         "harmony_channel"),
    ])
    def test_each_pattern(self, content, expected_pattern):
        hit = detect_leak(content)
        assert hit is not None
        assert hit.pattern == expected_pattern
        assert content[max(0, content.find("<")) : content.find("<") + 10] in hit.excerpt \
            or expected_pattern in ("json_name_block",)


class TestNoFalsePositive:
    @pytest.mark.parametrize("content", [
        "I think the answer is 20.",
        "Let me know if you'd like another option.",
        "Here is some HTML: <div>hi</div>",
        "Code in fenced block:\n```python\nprint('hi')\n```",
        "",
        None,
    ])
    def test_clean_text(self, content):
        assert detect_leak(content) is None
