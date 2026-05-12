"""Integration tests for ``/api/toollab`` — TestClient + fake LLM + fake DB.

Pattern matches ``test_api_prompts.py``: we don't open a real Postgres
connection (sqlalchemy.ext.asyncio.create_async_engine is patched) and we
override the auth + db dependencies with stubs.

Three routes are exercised end-to-end:

* ``POST /tools/validate`` — passes for clean defs, fails with structured
  errors for AST/schema violations.
* ``POST /run`` — happy path with a fake LLM that issues one tool call
  followed by a final answer. Verifies the trace is shaped correctly and
  no warnings are attached.
* ``POST /run`` — leak path. Fake LLM returns raw ``<tool_call>`` markup
  with no structured ``tool_calls`` → warning fires.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

# Patch infra (engine / oauth) BEFORE importing the FastAPI app — same
# pattern as tests/test_api_prompts.py.
with patch("authlib.integrations.starlette_client.OAuth.register"), \
     patch("sqlalchemy.ext.asyncio.create_async_engine"):
    from apps.api.main import app
    from libs.core import auth as core_auth
    from libs.core import database as core_db
    from libs.toollab import runner as toollab_runner
    from libs.toollab.registry import get_registry


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeAsyncSession:
    """Minimal AsyncSession stand-in — covers the calls the toollab router
    and runner make for the run / validate paths."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def execute(self, *_args, **_kw):
        # No DB-driven listing in these tests — return an empty result.
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        scalars = MagicMock()
        scalars.all.return_value = []
        result.scalars.return_value = scalars
        return result

    async def close(self):
        return None


async def _fake_get_db() -> AsyncIterator[_FakeAsyncSession]:
    yield _FakeAsyncSession()


def _fake_admin_user():
    return SimpleNamespace(
        sub="admin-1", username="admin", groups=["Admin"],
        is_admin=True, is_user=True,
    )


class _FakeToolLLM:
    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self._idx = 0
        # Last bind_tools kwargs — exposed for regression tests on
        # parallel_tool_calls / tool_choice passthrough.
        self.last_bind_kwargs: dict = {}

    def bind_tools(self, *_a, **kw):
        self.last_bind_kwargs = kw
        return self

    async def ainvoke(self, messages, **_kw):
        if self._idx >= len(self._responses):
            return AIMessage(content="(exhausted)")
        out = self._responses[self._idx]
        self._idx += 1
        return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app.dependency_overrides[core_auth.get_current_user] = _fake_admin_user
    app.dependency_overrides[core_db.get_db] = _fake_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _registry_clean():
    get_registry().clear()
    yield
    get_registry().clear()


# ---------------------------------------------------------------------------
# /tools/validate
# ---------------------------------------------------------------------------


class TestValidate:
    def _payload(self, code="def handler(x):\n    return {'out': x}\n"):
        return {
            "name": "echo",
            "description": "echo the integer back",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
                "additionalProperties": False,
            },
            "returns": {
                "type": "object",
                "properties": {"out": {"type": "integer"}},
                "required": ["out"],
            },
            "code": code,
            "tags": [],
        }

    def test_clean_payload_passes(self, client):
        r = client.post("/api/toollab/tools/validate", json=self._payload())
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["errors"] == []

    def test_import_rejected(self, client):
        bad = self._payload(
            code="import os\ndef handler(x):\n    return {'out': x}\n"
        )
        r = client.post("/api/toollab/tools/validate", json=bad)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert any("denied_node" in e["kind"] or "ast_" in e["kind"]
                   for e in body["errors"])

    def test_signature_mismatch_rejected(self, client):
        bad = self._payload(
            code="def handler(y):\n    return {'out': y}\n"  # 'y' instead of 'x'
        )
        r = client.post("/api/toollab/tools/validate", json=bad)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert any("args_mismatch" in e["kind"] for e in body["errors"])


# ---------------------------------------------------------------------------
# /run — happy path
# ---------------------------------------------------------------------------


class TestRunHappy:
    def _register_echo(self):
        reg = get_registry()
        td = SimpleNamespace(
            id=uuid.uuid4(),
            owner_user_id="admin-1",
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

    def test_tool_call_then_final_answer(self, client, monkeypatch):
        self._register_echo()
        responses = [
            AIMessage(
                content="",
                tool_calls=[{"id": "call-1", "name": "echo", "args": {"x": 7}}],
            ),
            AIMessage(content="The echo returned 7."),
        ]
        monkeypatch.setattr(
            toollab_runner.LLMGateway, "get_chat_llm",
            lambda *a, **k: _FakeToolLLM(responses),
        )

        r = client.post("/api/toollab/run", json={
            "prompt": "please echo 7",
            "model_type": "chat",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["iterations"] == 2
        assert body["truncated"] is False
        assert body["warnings"] == []
        assert body["final_response"] == "The echo returned 7."
        # Trace shape: ai (tool_call) → tool result → ai (final).
        kinds = [s["kind"] for s in body["steps"]]
        assert kinds == ["ai", "tool", "ai"]
        tool_step = body["steps"][1]
        assert tool_step["name"] == "echo"
        assert tool_step["ok"] is True
        assert tool_step["result"] == {"out": 7}

    def test_parallel_tool_calls_default_false(self, client, monkeypatch):
        """payload 에서 빠지면 default = False 가 bind_tools 로 전달돼야 한다."""
        self._register_echo()
        fake = _FakeToolLLM([AIMessage(content="done")])
        monkeypatch.setattr(
            toollab_runner.LLMGateway, "get_chat_llm",
            lambda *a, **k: fake,
        )
        r = client.post("/api/toollab/run", json={
            "prompt": "test",
            "model_type": "chat",
        })
        assert r.status_code == 200, r.text
        assert fake.last_bind_kwargs.get("parallel_tool_calls") is False

    def test_parallel_tool_calls_true_passthrough(self, client, monkeypatch):
        """payload 에서 True 로 보내면 그대로 전달돼야 한다."""
        self._register_echo()
        fake = _FakeToolLLM([AIMessage(content="done")])
        monkeypatch.setattr(
            toollab_runner.LLMGateway, "get_chat_llm",
            lambda *a, **k: fake,
        )
        r = client.post("/api/toollab/run", json={
            "prompt": "test",
            "model_type": "chat",
            "parallel_tool_calls": True,
        })
        assert r.status_code == 200, r.text
        assert fake.last_bind_kwargs.get("parallel_tool_calls") is True


# ---------------------------------------------------------------------------
# /run — parser-leak path
# ---------------------------------------------------------------------------


class TestRunLeak:
    def test_raw_markup_in_content_triggers_warning(self, client, monkeypatch):
        # No structured tool_calls — but the model put raw <tool_call> markup
        # in `content`. The runner should flag the parser-misconfig warning.
        leaked = AIMessage(
            content="Sure, I'll call: <tool_call>{name:'echo', args:{x:7}}</tool_call>"
        )
        monkeypatch.setattr(
            toollab_runner.LLMGateway, "get_chat_llm",
            lambda *a, **k: _FakeToolLLM([leaked]),
        )

        r = client.post("/api/toollab/run", json={
            "prompt": "please echo 7",
            "model_type": "chat",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert "tool_call_parser_likely_missing" in body["warnings"]
        # There's only one ai step (no tool calls) and no tool steps.
        assert [s["kind"] for s in body["steps"]] == ["ai"]


# ---------------------------------------------------------------------------
# /tools/generate-schemas
# ---------------------------------------------------------------------------


class TestGenerateSchemas:
    def test_happy_path_returns_schemas(self, client):
        code = (
            "def handler(x: int, name: str = 'a') -> dict:\n"
            "    return {'out': x}\n"
        )
        r = client.post("/api/toollab/tools/generate-schemas", json={"code": code})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["parameters"]["properties"]["x"] == {"type": "integer"}
        assert body["parameters"]["properties"]["name"] == {"type": "string"}
        assert body["parameters"]["required"] == ["x"]
        # `-> dict` 는 본문의 return literal 을 보고 properties 가 채워진다.
        assert body["returns"]["type"] == "object"
        assert body["returns"]["properties"]["out"] == {"type": "integer"}

    def test_missing_type_hint_returns_structured_error(self, client):
        code = "def handler(x) -> dict:\n    return {'out': x}\n"
        r = client.post("/api/toollab/tools/generate-schemas", json={"code": code})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["kind"] == "missing_param_annotation"
        assert "x" in body["error"]["detail"]

    def test_missing_return_annotation_errors(self, client):
        code = "def handler(x: int):\n    return {'out': x}\n"
        r = client.post("/api/toollab/tools/generate-schemas", json={"code": code})
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["kind"] == "missing_return_annotation"
