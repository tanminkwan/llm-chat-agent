"""
test_prompt_repository.py - PromptRepository 검색 필터 로직 단위 테스트
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from libs.core.repository import PromptRepository
from libs.core.models import Prompt


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def repo(mock_db):
    return PromptRepository(mock_db)


def _capture_executed_stmt(mock_db, return_rows):
    """db.execute 가 받은 stmt 를 capture 하고 mock result 를 반환하도록 셋업한다."""
    captured = {}

    async def _execute(stmt, *args, **kwargs):
        captured["stmt"] = stmt
        result = MagicMock()
        result.scalars.return_value.all.return_value = return_rows
        return result

    mock_db.execute.side_effect = _execute
    return captured


class TestPromptRepositorySearch:
    """search() 가 user_id / include_others / title_keyword 를 SQL where 절에 정확히 반영하는지 검증"""

    @pytest.mark.asyncio
    async def test_default_owner_only_no_keyword(self, repo, mock_db):
        captured = _capture_executed_stmt(mock_db, [
            Prompt(id=1, user_id="u1", title="my", content="c", is_public=False)
        ])

        result = await repo.search(user_id="u1")

        assert len(result) == 1
        assert result[0].user_id == "u1"
        sql = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
        upper = sql.upper()
        # 본인만 조건: WHERE 절에 user_id 비교만 있고 OR / is_public 분기는 없어야 함
        where_clause = upper.split("WHERE", 1)[1].split("ORDER BY")[0]
        assert "USER_ID" in where_clause
        assert "IS_PUBLIC" not in where_clause
        assert " OR " not in where_clause
        # 정렬은 updated_at 내림차순
        assert "ORDER BY" in upper
        assert "UPDATED_AT DESC" in upper

    @pytest.mark.asyncio
    async def test_include_others_uses_or_with_is_public(self, repo, mock_db):
        captured = _capture_executed_stmt(mock_db, [])

        await repo.search(user_id="u1", include_others=True)

        sql = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
        upper = sql.upper()
        where_clause = upper.split("WHERE", 1)[1].split("ORDER BY")[0]
        # OR 결합으로 본인 또는 공개 건 모두 노출
        assert "USER_ID" in where_clause
        assert "IS_PUBLIC" in where_clause
        assert " OR " in where_clause

    @pytest.mark.asyncio
    async def test_title_keyword_adds_ilike_filter(self, repo, mock_db):
        captured = _capture_executed_stmt(mock_db, [])

        await repo.search(user_id="u1", title_keyword="요약")

        sql = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
        # ILIKE 부분 일치 (% kw %) 가 적용되어야 함
        assert "ILIKE" in sql.upper() or "LIKE" in sql.upper()
        assert "%요약%" in sql

    @pytest.mark.asyncio
    async def test_returns_repository_rows_as_list(self, repo, mock_db):
        rows = [
            Prompt(id=1, user_id="u1", title="A", content="c", is_public=True),
            Prompt(id=2, user_id="u1", title="B", content="c", is_public=False),
        ]
        _capture_executed_stmt(mock_db, rows)

        result = await repo.search(user_id="u1")
        assert [p.id for p in result] == [1, 2]


class TestPromptRepositoryBaseCRUD:
    """BaseRepository 를 상속한 단순 CRUD 가 동작하는지 검증"""

    @pytest.mark.asyncio
    async def test_get_by_id(self, repo, mock_db):
        mock_db.get.return_value = Prompt(id=10, user_id="u1", title="t", content="c", is_public=True)
        result = await repo.get_by_id(10)
        assert result.id == 10
        mock_db.get.assert_called_once_with(Prompt, 10)

    @pytest.mark.asyncio
    async def test_create(self, repo, mock_db):
        await repo.create(
            user_id="u1",
            username="alice",
            title="my prompt",
            content="hello",
            is_public=True,
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update(self, repo, mock_db):
        prompt = Prompt(id=1, user_id="u1", title="old", content="c", is_public=True)
        await repo.update(prompt, title="new", is_public=False)
        assert prompt.title == "new"
        assert prompt.is_public is False
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete(self, repo, mock_db):
        prompt = Prompt(id=1, user_id="u1", title="t", content="c", is_public=True)
        await repo.delete(prompt)
        mock_db.delete.assert_called_once_with(prompt)
        mock_db.commit.assert_called_once()
