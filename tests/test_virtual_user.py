"""
test_virtual_user.py - 가상 사용자(virtual_user) 로그 마킹 테스트

- 비 로그인 모드: X-Virtual-User 헤더 값이 [LLM_LOG] 의 virtual_user 로 기록 (없으면 nobody)
- IDP 모드: 헤더는 무시되고 virtual_user == 인증된 user_id
- 인증 주체(user_id) 는 어느 경우에도 바뀌지 않음
"""
import json
import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage

# 인프라 연결 시도 차단 후 import
with patch("authlib.integrations.starlette_client.OAuth.register"), \
     patch("sqlalchemy.ext.asyncio.create_async_engine"):
    from apps.api.main import app
    from apps.api.schemas import UserInfo

from fastapi.testclient import TestClient

from libs.core import auth
from libs.core.logging_helpers import _virtual_user, emit_llm_log, get_virtual_user
from libs.core.settings import settings


@pytest.fixture(autouse=True)
def _reset_virtual_user():
    """테스트 간 ContextVar 누수 방지."""
    token = _virtual_user.set(None)
    yield
    _virtual_user.reset(token)


def _llm_logs(caplog):
    """caplog 의 모든 [LLM_LOG] 라인 JSON 을 dict list 로 반환."""
    out = []
    for r in caplog.records:
        m = re.search(r"\[LLM_LOG\]\s+(\{.*\})", r.getMessage())
        if m:
            out.append(json.loads(m.group(1)))
    return out


# --- emit_llm_log -------------------------------------------------------------

class TestEmitVirtualUser:
    def test_context_value_attached(self, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        _virtual_user.set("alice")
        emit_llm_log("debug", {"type": "request", "user_id": "nobody"})
        data = _llm_logs(caplog)[-1]
        assert data["user_id"] == "nobody"
        assert data["virtual_user"] == "alice"

    def test_fallback_to_user_id(self, caplog):
        """context 가 비어 있으면 payload 의 user_id 를 사용."""
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        emit_llm_log("debug", {"type": "request", "user_id": "u1"})
        assert _llm_logs(caplog)[-1]["virtual_user"] == "u1"

    def test_explicit_value_preserved(self, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        _virtual_user.set("alice")
        emit_llm_log("debug", {"type": "request", "virtual_user": "explicit"})
        assert _llm_logs(caplog)[-1]["virtual_user"] == "explicit"


# --- bind_virtual_user --------------------------------------------------------

class TestBindVirtualUser:
    def test_non_login_with_header(self):
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            auth.bind_virtual_user("nobody", "alice")
        assert get_virtual_user() == "alice"

    def test_non_login_without_header(self):
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            auth.bind_virtual_user("nobody", None)
        assert get_virtual_user() == "nobody"

    @pytest.mark.parametrize("bad", ["a b", "a\nb", "x" * 65, "한글", "a/b"])
    def test_non_login_invalid_header(self, bad):
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            with pytest.raises(HTTPException) as exc:
                auth.bind_virtual_user("nobody", bad)
        assert exc.value.status_code == 400

    def test_idp_ignores_header(self):
        with patch.object(settings, "NON_LOGIN_SERVICE", False):
            auth.bind_virtual_user("real-sub", "alice")
        assert get_virtual_user() == "real-sub"


# --- libs.core.auth.get_current_user (toollab 경로) ---------------------------

class TestAuthGetCurrentUser:
    async def test_non_login_binds_header(self):
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            user = await auth.get_current_user(MagicMock(), None, "alice")
        assert user.sub == "nobody"
        assert get_virtual_user() == "alice"

    async def test_direct_call_without_header_arg(self):
        """의존성을 함수로 직접 호출(헤더 인자 생략)해도 동작해야 한다."""
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            user = await auth.get_current_user(MagicMock(), None)
        assert user.sub == "nobody"
        assert get_virtual_user() == "nobody"

    @patch("libs.core.auth.jwt.decode")
    @patch("httpx.AsyncClient.get")
    async def test_idp_binds_authenticated_sub(self, mock_get, mock_decode):
        mock_get.return_value = AsyncMock(json=lambda: {"keys": []})
        mock_decode.return_value = {"sub": "user123", "preferred_username": "u", "groups": ["User"]}
        cred = MagicMock(credentials="dummy_token")
        with patch.object(settings, "NON_LOGIN_SERVICE", False):
            user = await auth.get_current_user(MagicMock(), cred, "alice")
        assert user.sub == "user123"
        assert get_virtual_user() == "user123"


# --- POST /api/chat/sync 통합 (실제 get_current_user 경유) --------------------

@pytest.fixture
def client():
    # lifespan(startup) 은 DB 를 쓰므로 실행하지 않는다 (with 블록 미사용).
    return TestClient(app)


@pytest.fixture
def mock_chat_llm():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(
        content="hi",
        usage_metadata={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
        response_metadata={"model_name": "mock-model"},
    ))
    with patch("libs.core.llm.LLMGateway.get_chat_llm", return_value=llm):
        yield llm


def _chat_sync(client, headers=None):
    return client.post(
        "/api/chat/sync",
        json={"message": "hello", "thread_id": "vu-test"},
        headers=headers or {},
    )


def _chat_logs(caplog):
    return [d for d in _llm_logs(caplog) if d["type"] in ("request", "response")]


class TestChatSyncVirtualUser:
    def test_non_login_with_header(self, client, mock_chat_llm, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            resp = _chat_sync(client, {"X-Virtual-User": "alice"})
        assert resp.status_code == 200
        assert resp.json()["content"] == "hi"

        logs = _chat_logs(caplog)
        assert [d["type"] for d in logs] == ["request", "response"]
        for d in logs:
            assert d["user_id"] == "nobody"
            assert d["virtual_user"] == "alice"

    def test_non_login_without_header(self, client, mock_chat_llm, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            resp = _chat_sync(client)
        assert resp.status_code == 200

        logs = _chat_logs(caplog)
        assert len(logs) == 2
        assert all(d["user_id"] == "nobody" and d["virtual_user"] == "nobody" for d in logs)

    def test_non_login_invalid_header(self, client, mock_chat_llm, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            resp = _chat_sync(client, {"X-Virtual-User": "bad value!"})
        assert resp.status_code == 400
        mock_chat_llm.ainvoke.assert_not_called()
        assert _chat_logs(caplog) == []

    def test_idp_header_ignored(self, client, mock_chat_llm, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        real_user = UserInfo(sub="real-sub", preferred_username="real", groups=["User"])
        with patch.object(settings, "NON_LOGIN_SERVICE", False), \
             patch("apps.api.api._authenticate", AsyncMock(return_value=real_user)):
            resp = _chat_sync(client, {"X-Virtual-User": "alice"})
        assert resp.status_code == 200

        logs = _chat_logs(caplog)
        assert len(logs) == 2
        assert all(d["user_id"] == "real-sub" and d["virtual_user"] == "real-sub" for d in logs)

    def test_streaming_chat_propagates(self, client, caplog):
        """StreamingResponse 생성기 내부 로그에도 virtual_user 가 전파되는지 확인."""
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        llm = MagicMock()

        async def astream(messages):
            yield AIMessage(content="hi")

        llm.astream = astream
        with patch.object(settings, "NON_LOGIN_SERVICE", True), \
             patch("libs.core.llm.LLMGateway.get_chat_llm", return_value=llm):
            resp = client.post(
                "/chat",
                json={"message": "hello", "thread_id": "vu-stream"},
                headers={"X-Virtual-User": "bob"},
            )
        assert resp.status_code == 200

        logs = _chat_logs(caplog)
        assert [d["type"] for d in logs] == ["request", "response"]
        assert all(d["virtual_user"] == "bob" for d in logs)


# --- 기본 thread_id 분리 / request_id 반환 -------------------------------------

class TestChatThreadAndRequestId:
    def test_default_thread_per_virtual_user(self, client, mock_chat_llm, caplog):
        """thread_id 미지정 시 가상 사용자별로 다른 쓰레드 → 대화 이력이 섞이지 않는다."""
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            for vu, msg in [("thr-alice", "alice-secret"), ("thr-bob", "bob-question")]:
                resp = client.post("/api/chat/sync", json={"message": msg},
                                   headers={"X-Virtual-User": vu})
                assert resp.status_code == 200

        reqs = [d for d in _llm_logs(caplog) if d["type"] == "request"]
        assert [d["thread_id"] for d in reqs] == ["user_thr-alice", "user_thr-bob"]
        bob_contents = [m["content"] for m in reqs[1]["messages"]]
        assert "alice-secret" not in bob_contents

    def test_explicit_thread_id_kept(self, client, mock_chat_llm, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            client.post("/api/chat/sync", json={"message": "hi", "thread_id": "my-thread"},
                        headers={"X-Virtual-User": "thr-carol"})
        assert all(d["thread_id"] == "my-thread" for d in _chat_logs(caplog))

    def test_idp_default_thread_unchanged(self, client, mock_chat_llm, caplog):
        """IDP 모드 기본 쓰레드는 기존과 동일하게 user_<sub>."""
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        real_user = UserInfo(sub="real-sub", preferred_username="real", groups=["User"])
        with patch.object(settings, "NON_LOGIN_SERVICE", False), \
             patch("apps.api.api._authenticate", AsyncMock(return_value=real_user)):
            client.post("/api/chat/sync", json={"message": "hi"}, headers={"X-Virtual-User": "x"})
        assert all(d["thread_id"] == "user_real-sub" for d in _chat_logs(caplog))

    def test_sync_returns_request_id(self, client, mock_chat_llm, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        with patch.object(settings, "NON_LOGIN_SERVICE", True):
            resp = _chat_sync(client, {"X-Virtual-User": "rid-alice"})
        assert resp.status_code == 200
        rid = resp.headers["X-Request-Id"]
        assert resp.json()["request_id"] == rid
        assert {d["request_id"] for d in _chat_logs(caplog)} == {rid}

    def test_sync_error_returns_request_id(self, client, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("upstream down"))
        with patch.object(settings, "NON_LOGIN_SERVICE", True), \
             patch("libs.core.llm.LLMGateway.get_chat_llm", return_value=llm):
            resp = _chat_sync(client, {"X-Virtual-User": "rid-err"})
        assert resp.status_code == 500
        rid = resp.headers["X-Request-Id"]
        err = [d for d in _llm_logs(caplog) if d["type"] == "error"]
        assert len(err) == 1 and err[0]["request_id"] == rid
        assert err[0]["virtual_user"] == "rid-err"

    def test_stream_returns_request_id(self, client, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        llm = MagicMock()

        async def astream(messages):
            yield AIMessage(content="hi")

        llm.astream = astream
        with patch.object(settings, "NON_LOGIN_SERVICE", True), \
             patch("libs.core.llm.LLMGateway.get_chat_llm", return_value=llm):
            resp = client.post("/chat", json={"message": "hello"},
                               headers={"X-Virtual-User": "rid-stream"})
        assert resp.status_code == 200
        rid = resp.headers["X-Request-Id"]
        logs = _chat_logs(caplog)
        assert {d["request_id"] for d in logs} == {rid}
        assert all(d["thread_id"] == "user_rid-stream" for d in logs)
