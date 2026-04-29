"""
test_service.py - Service 계층 단위 테스트 (SOLID: SRP + DIP 검증)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from libs.core.service import RAGService
from libs.core.models import Collection, Domain


@pytest.fixture
def mock_db():
    """Mock AsyncSession"""
    db = AsyncMock()
    db.add = MagicMock()
    # execute().scalars().all() 계층을 기본으로 모킹하여 테스트 간 충돌 방지
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute.return_value = mock_result
    return db


@pytest.fixture
def service(mock_db):
    """RAGService with mocked dependencies"""
    with patch("libs.core.service.QdrantClient") as mock_qdrant:
        svc = RAGService(mock_db)
        svc.qdrant = mock_qdrant.return_value
        yield svc


class TestRAGServiceCollection:
    """콜렉션 관련 비즈니스 로직 테스트"""

    @pytest.mark.asyncio
    async def test_list_collections(self, service, mock_db):
        """콜렉션 목록 조회"""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            Collection(collection_name="docs", name="문서함")
        ]
        mock_db.execute.return_value = mock_result

        result = await service.list_collections()
        assert len(result) == 1
        assert result[0].collection_name == "docs"

    @pytest.mark.asyncio
    async def test_create_collection(self, service, mock_db):
        """콜렉션 생성 시 collection_name 필수 포함"""
        await service.create_collection(
            collection_name="law_docs",
            name="법률 문서",
            description="법률 문서",
            snippet_size_limit=500,
            search_method="vector"
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_collection_immutable_name(self, service, mock_db):
        """콜렉션 수정 시 collection_name은 변경되지 않아야 함"""
        col = Collection(collection_name="test_col", name="이전 이름")
        mock_db.get.return_value = col

        # 식별자는 위치 인자로 넘기고, 수정할 데이터만 전달
        await service.update_collection("test_col", name="새 이름")
        
        assert col.name == "새 이름"
        assert col.collection_name == "test_col"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_collection_with_vector(self, service, mock_db):
        """콜렉션 삭제 시 Qdrant 데이터도 함께 삭제 (collection_name 기준)"""
        col = Collection(collection_name="law_docs", name="법률")
        mock_db.get.return_value = col
        service.qdrant.collection_exists.return_value = True

        await service.delete_collection("law_docs", delete_vector=True)

        service.qdrant.collection_exists.assert_called_once_with("law_docs")
        service.qdrant.delete_collection.assert_called_once_with("law_docs")
        mock_db.delete.assert_called_once_with(col)


class TestRAGServiceDomain:
    """도메인 관련 비즈니스 로직 테스트"""

    @pytest.mark.asyncio
    async def test_create_domain(self, service, mock_db):
        """도메인 생성"""
        await service.create_domain(name="과학")
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_domain(self, service, mock_db):
        """도메인 삭제"""
        dom = Domain(id=1, name="과학")
        mock_db.get.return_value = dom

        await service.delete_domain(1)
        mock_db.delete.assert_called_once_with(dom)
        mock_db.commit.assert_called_once()
