"""Tests for ``libs.toollab.runner.run`` with a fake LLM and fake DB.

Three behaviours that aren't visible at the unit level of any single
component:

* reasoning_content (gpt-oss style) flows into ``steps[*].reasoning``
* ``tool_calls=[]`` + raw ``<tool_call>...`` content flips on the
  ``tool_call_parser_likely_missing`` warning
* hitting ``max_tool_iterations`` produces ``truncated=True`` and exits
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from libs.toollab import runner
from libs.toollab.registry import ToolRegistry, get_registry
from libs.toollab.schemas import RunRequest


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeToolLLM:
    """Returns pre-baked ``AIMessage`` instances on each ``ainvoke``.

    ``bind_tools`` is a no-op (we don't need real tool binding to test the
    runner's loop control flow).
    """

    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self._idx = 0

    def bind_tools(self, *_a, **_kw):
        return self

    async def ainvoke(self, messages, **_kw):
        if self._idx >= len(self._responses):
            return AIMessage(content="(no more responses)")
        out = self._responses[self._idx]
        self._idx += 1
        return out


class _FakeDb:
    """Async session stub that ``runner.run`` can call ``.add()``/``.commit()`` on."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


def _fake_user(sub="user-1", admin=False):
    return SimpleNamespace(
        sub=sub, username=sub, groups=["Admin"] if admin else [],
        is_admin=admin, is_user=True,
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    reg = get_registry()
    reg.clear()
    yield
    reg.clear()


@pytest.fixture
def echo_tool():
    """Register a single 'echo' tool the fake LLM can call."""
    reg = get_registry()
    td = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id="user-1",
        name="echo",
        version=1,
        description="echo back",
        parameters_json={"type": "object",
                         "properties": {"x": {"type": "integer"}},
                         "required": ["x"]},
        returns_json={"type": "object",
                      "properties": {"out": {"type": "integer"}},
                      "required": ["out"]},
        code="def handler(x):\n    return {'out': x}\n",
    )
    reg.register(td)
    return td


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunner:
    @pytest.mark.asyncio
    async def test_reasoning_captured(self, monkeypatch):
        ai = AIMessage(content="answer is 42",
                       additional_kwargs={"reasoning_content": "I added 7+13+22"})
        monkeypatch.setattr(runner.LLMGateway, "get_chat_llm",
                            lambda *a, **k: _FakeToolLLM([ai]))

        req = RunRequest(prompt="add things", model_type="chat")
        db = _FakeDb()
        result = await runner.run(req, _fake_user(), db, request_id="req-1")

        assert result.iterations == 1
        assert result.truncated is False
        ai_steps = [s for s in result.steps if s.kind == "ai"]
        assert ai_steps and ai_steps[0].reasoning == "I added 7+13+22"
        assert result.final_response == "answer is 42"

    @pytest.mark.asyncio
    async def test_leak_warning(self, monkeypatch):
        # No tool_calls + raw markup in content → leak warning attached.
        ai = AIMessage(content="Sure, calling: <tool_call>{name:'echo'}</tool_call>")
        monkeypatch.setattr(runner.LLMGateway, "get_chat_llm",
                            lambda *a, **k: _FakeToolLLM([ai]))

        req = RunRequest(prompt="anything", model_type="chat")
        db = _FakeDb()
        result = await runner.run(req, _fake_user(), db, request_id="req-2")

        assert "tool_call_parser_likely_missing" in result.warnings

    @pytest.mark.asyncio
    async def test_max_iter_truncates(self, monkeypatch, echo_tool):
        # Every turn issues one tool call → loop will be capped by max_iter.
        def _ai_with_call() -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_a",
                    "name": "echo",
                    "args": {"x": 1},
                }],
            )

        # 5 calls — but we cap to 2 via request override.
        responses = [_ai_with_call() for _ in range(5)]
        monkeypatch.setattr(runner.LLMGateway, "get_chat_llm",
                            lambda *a, **k: _FakeToolLLM(responses))

        req = RunRequest(prompt="loop", model_type="chat",
                         max_tool_iterations=2)
        db = _FakeDb()
        result = await runner.run(req, _fake_user(), db, request_id="req-3")

        assert result.truncated is True
        assert result.iterations == 2
        # 2 AI + 2 tool steps interleaved → 4 total.
        assert len(result.steps) == 4
        ai_steps = [s for s in result.steps if s.kind == "ai"]
        tool_steps = [s for s in result.steps if s.kind == "tool"]
        assert len(ai_steps) == 2
        assert len(tool_steps) == 2

    @pytest.mark.asyncio
    async def test_no_tools_no_warnings(self, monkeypatch):
        """Plain answer with no tool_calls and no leak markers → clean trace."""
        ai = AIMessage(content="Nothing tool-shaped here, just prose.")
        monkeypatch.setattr(runner.LLMGateway, "get_chat_llm",
                            lambda *a, **k: _FakeToolLLM([ai]))

        req = RunRequest(prompt="say hi", model_type="chat")
        db = _FakeDb()
        result = await runner.run(req, _fake_user(), db, request_id="req-4")
        assert result.warnings == []
        assert result.final_response == ai.content
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_history_replayed_into_messages(self, monkeypatch):
        """Multi-turn: prior user/assistant/tool entries land in the LLM context."""
        captured: dict = {}

        class _CapturingLLM(_FakeToolLLM):
            async def ainvoke(self, messages, **kw):
                captured["messages"] = list(messages)
                return await super().ainvoke(messages, **kw)

        ai = AIMessage(content="ok")
        monkeypatch.setattr(runner.LLMGateway, "get_chat_llm",
                            lambda *a, **k: _CapturingLLM([ai]))

        history = [
            {"role": "user", "content": "first user prompt"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "call_x", "name": "echo",
                             "args": {"x": 9}}]},
            {"role": "tool", "tool_call_id": "call_x", "name": "echo",
             "content": '{"ok": true, "result": {"out": 9}}'},
            {"role": "assistant", "content": "I echoed 9."},
        ]
        req = RunRequest(prompt="now do this", model_type="chat",
                         history=history)
        db = _FakeDb()
        await runner.run(req, _fake_user(), db, request_id="req-5")

        msgs = captured["messages"]
        # System + 4 history entries + new HumanMessage(prompt) = 6
        assert len(msgs) == 6
        # Roles in order:
        from langchain_core.messages import (
            AIMessage as _AI, HumanMessage as _Hm, SystemMessage as _Sys,
            ToolMessage as _Tm,
        )
        assert isinstance(msgs[0], _Sys)
        assert isinstance(msgs[1], _Hm) and msgs[1].content == "first user prompt"
        assert isinstance(msgs[2], _AI)
        assert msgs[2].tool_calls[0]["id"] == "call_x"
        assert msgs[2].tool_calls[0]["name"] == "echo"
        assert isinstance(msgs[3], _Tm) and msgs[3].tool_call_id == "call_x"
        assert isinstance(msgs[4], _AI) and msgs[4].content == "I echoed 9."
        assert isinstance(msgs[5], _Hm) and msgs[5].content == "now do this"
