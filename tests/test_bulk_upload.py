import pytest
import pandas as pd
import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from apps.api.main import process_bulk_upload_task, global_bulk_tasks

@pytest.fixture
def mock_rag_service():
    service = MagicMock()
    # Mock col_repo.get_by_id
    mock_col = MagicMock()
    mock_col.snippet_size_limit = 500
    service.col_repo.get_by_id = AsyncMock(return_value=mock_col)
    
    # Mock add_knowledge_point
    service.add_knowledge_point = AsyncMock()
    return service

@pytest.mark.asyncio
async def test_process_bulk_upload_task_success(mock_rag_service):
    """정상적인 엑셀 파일이 주어졌을 때 전체 성공 및 Extended Content 자동 복사 검증"""
    # Given: 정상적인 데이터가 들어있는 엑셀 (두 번째 행은 Extended Content가 비어있음)
    df = pd.DataFrame({
        "Content": ["Test content 1", "Test content 2"],
        "Extended Content": ["Extended 1", ""]
    })
    
    excel_io = io.BytesIO()
    df.to_excel(excel_io, index=False)
    excel_bytes = excel_io.getvalue()
    
    task_id = str(uuid.uuid4())
    global_bulk_tasks[task_id] = {
        "total": 0, "success": 0, "error": 0, "done": False, "error_file": None
    }
    
    with patch("apps.api.main.AsyncSessionLocal") as mock_session_local, \
         patch("apps.api.main.RAGService", return_value=mock_rag_service):
        
        mock_session = AsyncMock()
        mock_session_local.return_value = mock_session
        
        # When: 백그라운드 태스크 실행
        await process_bulk_upload_task(task_id, excel_bytes, "test.xlsx", "my_col", 1)
        
    # Then: 2건 모두 성공
    assert global_bulk_tasks[task_id]["total"] == 2
    assert global_bulk_tasks[task_id]["success"] == 2
    assert global_bulk_tasks[task_id]["error"] == 0
    assert global_bulk_tasks[task_id]["done"] is True
    assert mock_rag_service.add_knowledge_point.call_count == 2
    
    # Extended Content 자동 복사 확인
    call_args = mock_rag_service.add_knowledge_point.call_args_list[1][1]
    assert call_args["content"] == "Test content 2"
    assert call_args["extended_content"] == "Test content 2"

@pytest.mark.asyncio
async def test_process_bulk_upload_task_error(mock_rag_service):
    """Snippet Size를 초과하는 데이터가 있을 때 Error 카운트 증가 및 에러 파일 생성 검증"""
    # Given: 하나는 정상, 하나는 500바이트 제한을 초과하는 데이터
    long_content = "A" * 600
    df = pd.DataFrame({
        "Content": ["Valid content", long_content],
        "Extended Content": ["Ext", "Ext"]
    })
    
    excel_io = io.BytesIO()
    df.to_excel(excel_io, index=False)
    excel_bytes = excel_io.getvalue()
    
    task_id = str(uuid.uuid4())
    global_bulk_tasks[task_id] = {
        "total": 0, "success": 0, "error": 0, "done": False, "error_file": None
    }
    
    with patch("apps.api.main.AsyncSessionLocal") as mock_session_local, \
         patch("apps.api.main.RAGService", return_value=mock_rag_service):
        
        mock_session = AsyncMock()
        mock_session_local.return_value = mock_session
        
        # When: 백그라운드 태스크 실행
        await process_bulk_upload_task(task_id, excel_bytes, "error.xlsx", "my_col", 1)
        
    # Then: 1건 성공, 1건 실패 및 에러 파일 생성
    assert global_bulk_tasks[task_id]["total"] == 2
    assert global_bulk_tasks[task_id]["success"] == 1
    assert global_bulk_tasks[task_id]["error"] == 1
    assert global_bulk_tasks[task_id]["done"] is True
    assert global_bulk_tasks[task_id]["error_file"] is not None
    assert mock_rag_service.add_knowledge_point.call_count == 1
