"""Tests for ``libs.toollab.serializer``.

The most important assertion here is the **T5c invariant**: the serialised
content always has ``{`` as its first character — never a bare JSON array.
"""
from __future__ import annotations

import json

from libs.toollab.serializer import to_tool_message_content


class TestObjectWrappingInvariant:
    """The output's first char is always ``{``."""

    def test_scalar_int(self):
        out = to_tool_message_content({"ok": True, "result": 5})
        assert out.startswith("{")
        assert json.loads(out) == {"ok": True, "result": 5}

    def test_scalar_str(self):
        out = to_tool_message_content({"ok": True, "result": "hello"})
        assert out.startswith("{")
        assert json.loads(out)["result"] == "hello"

    def test_scalar_none(self):
        out = to_tool_message_content({"ok": True, "result": None})
        assert out.startswith("{")
        assert json.loads(out) == {"ok": True, "result": None}

    def test_list_result_is_wrapped(self):
        # The exact failure mode that motivated this serializer (T5c).
        bare_list = [{"name": "Alice", "email": "alice@example.com"}]
        out = to_tool_message_content({"ok": True, "result": bare_list})
        assert out.startswith("{"), "list result must be wrapped, not bare array"
        assert json.loads(out)["result"] == bare_list

    def test_dict_result_is_wrapped(self):
        dict_result = {"users": [{"name": "Bob"}]}
        out = to_tool_message_content({"ok": True, "result": dict_result})
        assert out.startswith("{")
        assert json.loads(out)["result"] == dict_result


class TestErrorPaths:
    def test_error_flag(self):
        out = to_tool_message_content({
            "ok": False,
            "error_kind": "timeout",
            "error": "exceeded 2000 ms",
        })
        d = json.loads(out)
        assert d == {"ok": False, "error_kind": "timeout", "error": "exceeded 2000 ms"}
        assert out.startswith("{")

    def test_korean_not_ascii_escaped(self):
        out = to_tool_message_content({"ok": True, "result": "안녕하세요"})
        assert "안녕하세요" in out
        assert "\\u" not in out
