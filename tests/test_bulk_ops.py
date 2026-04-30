import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Global mocks before importing app to avoid network calls
with patch("authlib.integrations.starlette_client.OAuth.register"), \
     patch("apps.api.main.engine.begin"), \
     patch("apps.api.main.AsyncSessionLocal"):
    from apps.api.main import app, get_current_user, get_rag_service
    from apps.api.schemas import UserInfo, SearchResult, SearchRequest

from fastapi.testclient import TestClient

# Mock Admin user for API testing
def mock_admin_user():
    return UserInfo(sub="admin_sub", preferred_username="admin_user", groups=["Admin"])

@pytest.fixture(autouse=True)
def mock_qdrant():
    with patch("libs.core.service.QdrantClient") as mock:
        yield mock

@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = mock_admin_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def mock_service():
    service = MagicMock()
    service.count_knowledge_points = AsyncMock()
    service.bulk_delete_knowledge_points = AsyncMock()
    service.search_rag = AsyncMock()
    return service

def test_get_delete_count_api(client, mock_service):
    """일괄 삭제 전 건수 조회 API 테스트"""
    app.dependency_overrides[get_rag_service] = lambda: mock_service
    mock_service.count_knowledge_points.return_value = 10
    
    response = client.get("/api/rag/delete-count", params={
        "collection": "test_col",
        "domain_id": 1,
        "source": "test.xlsx"
    })
    
    assert response.status_code == 200
    assert response.json() == {"count": 10}
    mock_service.count_knowledge_points.assert_called_once_with("test_col", 1, "test.xlsx")

def test_bulk_delete_api(client, mock_service):
    """일괄 삭제 실행 API 테스트"""
    app.dependency_overrides[get_rag_service] = lambda: mock_service
    mock_service.bulk_delete_knowledge_points.return_value = {"message": "Bulk delete success"}
    
    response = client.delete("/api/rag/bulk-delete", params={
        "collection": "test_col",
        "domain_id": 1,
        "source": "test.xlsx"
    })
    
    assert response.status_code == 200
    # Update expected message to match MessageResponse schema if needed
    assert "message" in response.json()
    mock_service.bulk_delete_knowledge_points.assert_called_once_with("test_col", 1, "test.xlsx")

def test_bulk_delete_api_permission_denied(client):
    """Admin이 아닌 유저의 일괄 삭제 요청 거부 테스트"""
    def mock_normal_user():
        return UserInfo(sub="user_sub", preferred_username="user", groups=["User"])
    
    app.dependency_overrides[get_current_user] = mock_normal_user
    
    response = client.delete("/api/rag/bulk-delete", params={
        "collection": "test_col"
    })
    
    assert response.status_code == 403

def test_search_rag_api(client, mock_service):
    """RAG 검색 API POST 방식 테스트"""
    app.dependency_overrides[get_rag_service] = lambda: mock_service
    mock_service.search_rag.return_value = [
        {
            "id": "point-1",
            "collection": "test_col",
            "score": 0.9,
            "content": "검색 결과",
            "extended_content": "상세 결과",
            "domain_id": 1,
            "source": "test.pdf",
            "created_at": "2024-04-24T00:00:00Z"
        }
    ]
    
    payload = {
        "query": "검색어",
        "collection_id": "test_col",
        "limit": 5
    }
    
    response = client.post("/api/rag/search", json=payload)
    
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == "point-1"
    mock_service.search_rag.assert_called_once()

# --- Service Layer Unit Tests ---

@pytest.mark.asyncio
async def test_service_count_knowledge_points(mock_qdrant):
    """RAGService.count_knowledge_points 로직 테스트"""
    from libs.core.service import RAGService
    db = AsyncMock()
    service = RAGService(db)
    q_inst = mock_qdrant.return_value
    service.qdrant = q_inst
    
    mock_res = MagicMock()
    mock_res.count = 5
    q_inst.count.return_value = mock_res
    
    count = await service.count_knowledge_points("my_col", domain_id=2, source="file.xlsx")
    
    assert count == 5
    q_inst.count.assert_called_once()

@pytest.mark.asyncio
async def test_service_bulk_delete_knowledge_points(mock_qdrant):
    """RAGService.bulk_delete_knowledge_points 로직 테스트"""
    from libs.core.service import RAGService
    db = AsyncMock()
    service = RAGService(db)
    q_inst = mock_qdrant.return_value
    service.qdrant = q_inst
    
    await service.bulk_delete_knowledge_points("my_col", domain_id=2, source="file.xlsx")
    
    q_inst.delete.assert_called_once()
