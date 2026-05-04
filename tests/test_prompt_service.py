"""
test_prompt_service.py - PromptService 권한/예외 분기 단위 테스트
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from libs.core.service import PromptService
from libs.core.models import Prompt


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def service(mock_db):
    svc = PromptService(mock_db)
    # 내부 repo 의 search/get/create/update/delete 를 직접 모킹해 SQL 의존성 제거
    svc.repo = AsyncMock()
    return svc


class TestPromptServiceList:
    @pytest.mark.asyncio
    async def test_list_passes_filters_to_repo(self, service):
        service.repo.search.return_value = []
        await service.list_prompts(user_id="u1", include_others=True, title_keyword="kw")
        service.repo.search.assert_awaited_once_with(
            user_id="u1", include_others=True, title_keyword="kw"
        )

    @pytest.mark.asyncio
    async def test_list_returns_repo_rows(self, service):
        rows = [
            Prompt(id=1, user_id="u1", title="A", content="c", is_public=True),
        ]
        service.repo.search.return_value = rows
        result = await service.list_prompts(user_id="u1")
        assert result == rows


class TestPromptServiceGet:
    @pytest.mark.asyncio
    async def test_get_owner(self, service):
        prompt = Prompt(id=1, user_id="owner", title="t", content="c", is_public=False)
        service.repo.get_by_id.return_value = prompt
        result = await service.get_prompt(1, user_id="owner")
        assert result is prompt

    @pytest.mark.asyncio
    async def test_get_public_other(self, service):
        prompt = Prompt(id=1, user_id="owner", title="t", content="c", is_public=True)
        service.repo.get_by_id.return_value = prompt
        result = await service.get_prompt(1, user_id="other")
        assert result is prompt

    @pytest.mark.asyncio
    async def test_get_private_other_raises_permission(self, service):
        prompt = Prompt(id=1, user_id="owner", title="t", content="c", is_public=False)
        service.repo.get_by_id.return_value = prompt
        with pytest.raises(PermissionError):
            await service.get_prompt(1, user_id="other")

    @pytest.mark.asyncio
    async def test_get_missing_raises_value_error(self, service):
        service.repo.get_by_id.return_value = None
        with pytest.raises(ValueError):
            await service.get_prompt(999, user_id="u1")


class TestPromptServiceCreate:
    @pytest.mark.asyncio
    async def test_create_passes_all_fields(self, service):
        await service.create_prompt(
            user_id="u1",
            username="alice",
            title="t",
            content="c",
            is_public=False,
        )
        service.repo.create.assert_awaited_once_with(
            user_id="u1",
            username="alice",
            title="t",
            content="c",
            is_public=False,
        )


class TestPromptServiceUpdate:
    @pytest.mark.asyncio
    async def test_update_owner(self, service):
        prompt = Prompt(id=1, user_id="owner", title="old", content="c", is_public=True)
        service.repo.get_by_id.return_value = prompt
        service.repo.update.return_value = prompt

        await service.update_prompt(1, user_id="owner", title="new", content="c2", is_public=False)

        service.repo.update.assert_awaited_once_with(
            prompt, title="new", content="c2", is_public=False
        )

    @pytest.mark.asyncio
    async def test_update_other_raises_permission(self, service):
        prompt = Prompt(id=1, user_id="owner", title="t", content="c", is_public=True)
        service.repo.get_by_id.return_value = prompt

        with pytest.raises(PermissionError):
            await service.update_prompt(1, user_id="other", title="x", content="x", is_public=True)

        service.repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_missing_raises_value_error(self, service):
        service.repo.get_by_id.return_value = None
        with pytest.raises(ValueError):
            await service.update_prompt(999, user_id="u1", title="t", content="c", is_public=True)


class TestPromptServiceDelete:
    @pytest.mark.asyncio
    async def test_delete_owner(self, service):
        prompt = Prompt(id=1, user_id="owner", title="t", content="c", is_public=True)
        service.repo.get_by_id.return_value = prompt
        await service.delete_prompt(1, user_id="owner")
        service.repo.delete.assert_awaited_once_with(prompt)

    @pytest.mark.asyncio
    async def test_delete_other_raises_permission(self, service):
        prompt = Prompt(id=1, user_id="owner", title="t", content="c", is_public=True)
        service.repo.get_by_id.return_value = prompt
        with pytest.raises(PermissionError):
            await service.delete_prompt(1, user_id="other")
        service.repo.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_missing_raises_value_error(self, service):
        service.repo.get_by_id.return_value = None
        with pytest.raises(ValueError):
            await service.delete_prompt(999, user_id="u1")
