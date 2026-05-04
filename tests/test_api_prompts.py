"""
test_api_prompts.py - /api/prompts FastAPI 엔드포인트 통합 테스트
- DB / 서비스 계층은 in-memory 스토어로 모킹
- 인증은 dependency_overrides 로 bypass
"""
import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# 인프라 연결 차단 후 import
with patch("authlib.integrations.starlette_client.OAuth.register"), \
     patch("sqlalchemy.ext.asyncio.create_async_engine"):
    from apps.api.main import app, get_current_user as main_get_current_user, get_prompt_service
    from apps.api.schemas import UserInfo

from fastapi.testclient import TestClient


def _make_prompt(id, user_id, title, content="body", is_public=True, username=None):
    """Service 계층이 반환하는 Prompt 처럼 동작할 dict-attr 객체"""
    obj = MagicMock()
    obj.id = id
    obj.user_id = user_id
    obj.username = username or user_id
    obj.title = title
    obj.content = content
    obj.is_public = is_public
    obj.created_at = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    obj.updated_at = datetime.datetime(2026, 1, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)
    return obj


class FakePromptService:
    """PromptService 의 in-memory 대체. 권한 분기는 service.py 와 동일하게 재현."""

    def __init__(self, store=None, next_id=100):
        # store: id -> prompt
        self.store = store if store is not None else {}
        self._next_id = next_id

    async def list_prompts(self, user_id, include_others=False, title_keyword=None):
        rows = []
        for p in self.store.values():
            if p.user_id == user_id or (include_others and p.is_public):
                if title_keyword and title_keyword.lower() not in p.title.lower():
                    continue
                rows.append(p)
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    async def get_prompt(self, prompt_id, user_id):
        p = self.store.get(prompt_id)
        if not p:
            raise ValueError("Prompt not found")
        if p.user_id != user_id and not p.is_public:
            raise PermissionError("조회 권한이 없습니다.")
        return p

    async def create_prompt(self, user_id, username, title, content, is_public=True):
        new_id = self._next_id
        self._next_id += 1
        p = _make_prompt(new_id, user_id, title, content, is_public, username)
        self.store[new_id] = p
        return p

    async def update_prompt(self, prompt_id, user_id, title, content, is_public):
        p = self.store.get(prompt_id)
        if not p:
            raise ValueError("Prompt not found")
        if p.user_id != user_id:
            raise PermissionError("수정 권한이 없습니다.")
        p.title = title
        p.content = content
        p.is_public = is_public
        p.updated_at = datetime.datetime(2026, 1, 3, 12, 0, 0, tzinfo=datetime.timezone.utc)
        return p

    async def delete_prompt(self, prompt_id, user_id):
        p = self.store.get(prompt_id)
        if not p:
            raise ValueError("Prompt not found")
        if p.user_id != user_id:
            raise PermissionError("삭제 권한이 없습니다.")
        self.store.pop(prompt_id, None)


def _override_user(sub="u1", username="alice", groups=None):
    groups = groups or ["User"]
    def _dep():
        return UserInfo(sub=sub, preferred_username=username, groups=groups)
    return _dep


@pytest.fixture
def fake_service():
    return FakePromptService()


@pytest.fixture
def client(fake_service):
    app.dependency_overrides[main_get_current_user] = _override_user()
    app.dependency_overrides[get_prompt_service] = lambda: fake_service
    # TestClient 를 컨텍스트 매니저 없이 생성하여 startup 이벤트(DB 메타 생성)를 우회
    yield TestClient(app)
    app.dependency_overrides.clear()


def _set_user(sub, username="user", groups=None):
    """런타임에 인증 사용자 변경"""
    app.dependency_overrides[main_get_current_user] = _override_user(sub, username, groups)


# ---------- list ----------

class TestListPrompts:
    def test_returns_only_owner_when_include_others_false(self, client, fake_service):
        fake_service.store = {
            1: _make_prompt(1, "u1", "내 것"),
            2: _make_prompt(2, "u2", "타인 공개", is_public=True),
            3: _make_prompt(3, "u2", "타인 비공개", is_public=False),
        }
        res = client.get("/api/prompts")
        assert res.status_code == 200
        data = res.json()
        assert [p["id"] for p in data] == [1]
        assert data[0]["is_owner"] is True

    def test_include_others_returns_owner_plus_public(self, client, fake_service):
        fake_service.store = {
            1: _make_prompt(1, "u1", "내 것"),
            2: _make_prompt(2, "u2", "타인 공개", is_public=True),
            3: _make_prompt(3, "u2", "타인 비공개", is_public=False),
        }
        res = client.get("/api/prompts?include_others=true")
        assert res.status_code == 200
        data = res.json()
        ids = sorted(p["id"] for p in data)
        assert ids == [1, 2]
        # 타인 공개 건은 is_owner=false
        for p in data:
            if p["id"] == 2:
                assert p["is_owner"] is False
            else:
                assert p["is_owner"] is True

    def test_title_keyword_filter(self, client, fake_service):
        fake_service.store = {
            1: _make_prompt(1, "u1", "요약 프롬프트"),
            2: _make_prompt(2, "u1", "분류 프롬프트"),
        }
        res = client.get("/api/prompts?title=요약")
        assert res.status_code == 200
        data = res.json()
        assert [p["id"] for p in data] == [1]

    def test_unauthorized_group_returns_403(self, client, fake_service):
        _set_user("u1", "guest", groups=["Guest"])
        res = client.get("/api/prompts")
        assert res.status_code == 403


# ---------- get one ----------

class TestGetPrompt:
    def test_owner_can_view(self, client, fake_service):
        fake_service.store = {1: _make_prompt(1, "u1", "내 것", is_public=False)}
        res = client.get("/api/prompts/1")
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == 1
        assert body["is_owner"] is True

    def test_other_can_view_public(self, client, fake_service):
        fake_service.store = {1: _make_prompt(1, "u2", "공개", is_public=True)}
        res = client.get("/api/prompts/1")
        assert res.status_code == 200
        assert res.json()["is_owner"] is False

    def test_other_cannot_view_private(self, client, fake_service):
        fake_service.store = {1: _make_prompt(1, "u2", "비공개", is_public=False)}
        res = client.get("/api/prompts/1")
        assert res.status_code == 403

    def test_not_found(self, client, fake_service):
        res = client.get("/api/prompts/999")
        assert res.status_code == 404


# ---------- create ----------

class TestCreatePrompt:
    def test_create_success_default_public(self, client, fake_service):
        res = client.post("/api/prompts", json={
            "title": "새 프롬프트",
            "content": "본문",
            "is_public": True,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["title"] == "새 프롬프트"
        assert body["is_public"] is True
        assert body["user_id"] == "u1"
        assert body["username"] == "alice"
        assert body["is_owner"] is True
        # service 가 store 에 보관했는지
        assert any(p.title == "새 프롬프트" for p in fake_service.store.values())

    def test_create_validation_missing_title(self, client):
        res = client.post("/api/prompts", json={"content": "x"})
        assert res.status_code == 422

    def test_create_validation_missing_content(self, client):
        res = client.post("/api/prompts", json={"title": "t"})
        assert res.status_code == 422


# ---------- update ----------

class TestUpdatePrompt:
    def test_owner_can_update(self, client, fake_service):
        fake_service.store = {1: _make_prompt(1, "u1", "old", "old-c", True)}
        res = client.put("/api/prompts/1", json={
            "title": "new",
            "content": "new-c",
            "is_public": False,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["title"] == "new"
        assert body["is_public"] is False

    def test_other_cannot_update(self, client, fake_service):
        fake_service.store = {1: _make_prompt(1, "u2", "old", "old-c", True)}
        res = client.put("/api/prompts/1", json={
            "title": "hacked",
            "content": "x",
            "is_public": True,
        })
        assert res.status_code == 403

    def test_update_missing(self, client, fake_service):
        res = client.put("/api/prompts/999", json={
            "title": "t", "content": "c", "is_public": True,
        })
        assert res.status_code == 404


# ---------- delete ----------

class TestDeletePrompt:
    def test_owner_can_delete(self, client, fake_service):
        fake_service.store = {1: _make_prompt(1, "u1", "t")}
        res = client.delete("/api/prompts/1")
        assert res.status_code == 200
        assert 1 not in fake_service.store

    def test_other_cannot_delete(self, client, fake_service):
        fake_service.store = {1: _make_prompt(1, "u2", "t")}
        res = client.delete("/api/prompts/1")
        assert res.status_code == 403
        assert 1 in fake_service.store

    def test_delete_missing(self, client):
        res = client.delete("/api/prompts/999")
        assert res.status_code == 404
