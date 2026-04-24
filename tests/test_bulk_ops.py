import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from apps.api.main import app, get_current_user, get_rag_service, UserInfo

# Mock Admin user for API testing
def mock_admin_user():
    return UserInfo(sub="admin_sub", preferred_username="admin_user", groups=["Admin"])

@pytest.fixture(autouse=True)
def mock_qdrant():
    with patch("libs.core.service.QdrantClient") as mock:
        yield mock

@pytest.fixture(autouse=True)
def mock_db_engine():
    with patch("apps.api.main.engine") as mock_eng, \
         patch("apps.api.main.AsyncSessionLocal") as mock_sess:
        yield mock_eng, mock_sess

@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = mock_admin_user
    # startup 이벤트에서 발생하는 DB 연동을 방지하기 위해 TestClient(app) 호출 시 
    # autouse fixture가 먼저 동작함
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def mock_service():
    service = MagicMock()
    service.count_knowledge_points = AsyncMock()
    service.bulk_delete_knowledge_points = AsyncMock()
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
    mock_service.bulk_delete_knowledge_points.return_value = {"status": "success"}
    
    response = client.delete("/api/rag/bulk-delete", params={
        "collection": "test_col",
        "domain_id": 1,
        "source": "test.xlsx"
    })
    
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    mock_service.bulk_delete_knowledge_points.assert_called_once_with("test_col", 1, "test.xlsx")

def test_bulk_delete_api_permission_denied(mock_qdrant):
    """Admin이 아닌 유저의 일괄 삭제 요청 거부 테스트"""
    def mock_normal_user():
        return UserInfo(sub="user_sub", preferred_username="user", groups=["User"])
    
    app.dependency_overrides[get_current_user] = mock_normal_user
    # TestClient 생성 시 startup 이벤트 방지를 위해 with 구문 없이 사용하거나
    # startup 이벤트를 모킹함
    test_client = TestClient(app)
    
    response = test_client.delete("/api/rag/bulk-delete", params={
        "collection": "test_col"
    })
    
    assert response.status_code == 403
    app.dependency_overrides.clear()

# --- Service Layer Unit Tests ---

@pytest.mark.asyncio
async def test_service_count_knowledge_points(mock_qdrant):
    """RAGService.count_knowledge_points 로직 테스트"""
    from libs.core.service import RAGService
    db = AsyncMock()
    service = RAGService(db)
    # mock_qdrant fixture handles the class, we need the instance
    q_inst = mock_qdrant.return_value
    service.qdrant = q_inst
    
    # Mock Qdrant count response
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

