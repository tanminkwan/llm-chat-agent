"""
test_repository.py - Repository 계층 단위 테스트 (SOLID: SRP 검증)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from libs.core.repository import BaseRepository, CollectionRepository, DomainRepository
from libs.core.models import Collection, Domain


@pytest.fixture
def mock_db():
    """Mock AsyncSession"""
    db = AsyncMock()
    db.add = MagicMock()
    return db


class TestBaseRepository:
    """BaseRepository Generic CRUD 테스트"""

    @pytest.mark.asyncio
    async def test_get_all(self, mock_db):
        """전체 조회가 정상 동작하는지 검증"""
        repo = CollectionRepository(mock_db)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            Collection(collection_name="qna", name="Q&A")
        ]
        mock_db.execute.return_value = mock_result

        result = await repo.get_all()
        assert len(result) == 1
        assert result[0].collection_name == "qna"
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_id(self, mock_db):
        """식별자(collection_name)로 단건 조회가 정상 동작하는지 검증"""
        repo = CollectionRepository(mock_db)
        mock_db.get.return_value = Collection(collection_name="qna", name="Q&A")

        # 이제 ID 대신 collection_name 문자열을 전달
        result = await repo.get_by_id("qna")
        assert result.name == "Q&A"
        assert result.collection_name == "qna"
        mock_db.get.assert_called_once_with(Collection, "qna")

    @pytest.mark.asyncio
    async def test_create_collection(self, mock_db):
        """새 콜렉션 생성이 정상 동작하는지 검증"""
        repo = CollectionRepository(mock_db)

        await repo.create(
            collection_name="test_col",
            name="테스트 콜렉션",
            description="설명",
            snippet_size_limit=500,
            search_method="vector"
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_collection(self, mock_db):
        """콜렉션 수정이 정상 동작하는지 검증"""
        repo = CollectionRepository(mock_db)
        col = Collection(collection_name="test_col", name="이전 이름")

        await repo.update(col, name="새 이름")
        assert col.name == "새 이름"
        assert col.collection_name == "test_col" # 식별자는 유지
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete(self, mock_db):
        """레코드 삭제가 정상 동작하는지 검증"""
        repo = DomainRepository(mock_db)
        domain = Domain(id=1, name="과학")

        await repo.delete(domain)
        mock_db.delete.assert_called_once_with(domain)
        mock_db.commit.assert_called_once()
