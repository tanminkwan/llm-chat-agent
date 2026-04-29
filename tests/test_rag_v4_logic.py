import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from libs.core.service import RAGService
from libs.core.models import Collection

@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    # execute().scalars().all() 계층을 기본으로 모킹
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute.return_value = mock_result
    return db

@pytest.fixture
def service(mock_db):
    with patch("libs.core.service.QdrantClient") as mock_qdrant, \
         patch("libs.core.service.LLMGateway") as mock_llm:
        
        # Mock Embeddings
        mock_embeddings = MagicMock()
        mock_embeddings.embed_query.return_value = [0.1, 0.2, 0.3]
        mock_llm.get_embeddings.return_value = mock_embeddings
        
        svc = RAGService(mock_db)
        svc.qdrant = mock_qdrant.return_value
        svc.embeddings = mock_embeddings
        yield svc

class TestRAGServiceV4:
    """Phase 4 추가 기능 테스트 (통합 검색 및 지식 관리)"""

    @pytest.mark.asyncio
    async def test_search_rag_with_query(self, service, mock_db):
        """쿼리가 있는 경우 벡터 검색 수행 검증"""
        # Given: 콜렉션 목록 모킹
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            Collection(collection_name="test_col", name="테스트")
        ]
        mock_db.execute.return_value = mock_result
        service.qdrant.collection_exists.return_value = True
        
        # Mock Qdrant query_points result
        mock_hit = MagicMock()
        mock_hit.id = "uuid-123"
        mock_hit.score = 0.95
        mock_hit.payload = {
            "content": "테스트 내용",
            "extended_content": "전체 테스트 내용",
            "domain_id": 1,
            "source": "manual"
        }
        mock_res = MagicMock()
        mock_res.points = [mock_hit]
        service.qdrant.query_points.return_value = mock_res

        # When: 검색 수행
        results = await service.search_rag(query="안녕", collection_id="test_col")

        # Then: 검색 결과 및 필드 검증
        assert len(results) == 1
        assert results[0]["id"] == "uuid-123"
        assert results[0]["extended_content"] == "전체 테스트 내용"
        assert results[0]["source"] == "manual"
        service.qdrant.query_points.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_rag_without_query(self, service, mock_db):
        """쿼리가 없는 경우 스크롤(전체 조회) 수행 검증"""
        # Given: 콜렉션 목록 모킹
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            Collection(collection_name="test_col", name="테스트")
        ]
        mock_db.execute.return_value = mock_result
        service.qdrant.collection_exists.return_value = True
        
        # Mock Qdrant scroll result
        mock_point = MagicMock()
        mock_point.id = "uuid-456"
        mock_point.payload = {"content": "스크롤 데이터"}
        service.qdrant.scroll.return_value = ([mock_point], None)

        # When: 쿼리 없이 검색
        results = await service.search_rag(query=None, collection_id="test_col")

        # Then: 스크롤 호출 여부 확인
        service.qdrant.scroll.assert_called_once()
        assert results[0]["content"] == "스크롤 데이터"

    @pytest.mark.asyncio
    async def test_search_rag_sorting_by_created_at(self, service, mock_db):
        """쿼리가 없는 경우 created_at 내림차순 정렬 검증"""
        # Given
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            Collection(collection_name="test_col", name="테스트")
        ]
        mock_db.execute.return_value = mock_result
        service.qdrant.collection_exists.return_value = True
        
        # Mock Qdrant scroll results (순서가 섞여서 들어온다고 가정)
        p1 = MagicMock(id="1", payload={"content": "옛날데이터", "created_at": "2024-01-01T00:00:00Z"})
        p2 = MagicMock(id="2", payload={"content": "최신데이터", "created_at": "2024-04-24T00:00:00Z"})
        service.qdrant.scroll.return_value = ([p1, p2], None)

        # When
        results = await service.search_rag(query=None, collection_id="test_col")

        # Then: 최신 데이터(p2)가 첫 번째여야 함
        assert results[0]["id"] == "2"
        assert results[1]["id"] == "1"

    @pytest.mark.asyncio
    async def test_add_knowledge_point_create(self, service):
        """새로운 지식 데이터 등록 시 created_at 생성 검증"""
        # Given
        service.qdrant.collection_exists.return_value = True

        # When
        res = await service.add_knowledge_point(
            collection_name="test_col",
            domain_id=1,
            content="임베딩 문구",
            extended_content="확장 내용",
            source="test_file.pdf"
        )

        # Then
        assert "created_at" in res
        args, kwargs = service.qdrant.upsert.call_args
        payload = kwargs['points'][0].payload
        assert "created_at" in payload

    @pytest.mark.asyncio
    async def test_add_knowledge_point_update(self, service):
        """기존 지식 데이터 수정(ID 지정) 검증"""
        # Given
        service.qdrant.collection_exists.return_value = True
        fixed_id = "existing-uuid"

        # When
        await service.add_knowledge_point(
            collection_name="test_col",
            domain_id=1,
            content="수정 문구",
            extended_content="수정 내용",
            source="manual",
            point_id=fixed_id
        )

        # Then: 지정된 ID로 upsert 되었는지 확인
        args, kwargs = service.qdrant.upsert.call_args
        assert kwargs['points'][0].id == fixed_id

    @pytest.mark.asyncio
    async def test_delete_knowledge_point(self, service):
        """지식 데이터 삭제 검증"""
        # When
        await service.delete_knowledge_point("test_col", "point-123")

        # Then
        service.qdrant.delete.assert_called_once()
