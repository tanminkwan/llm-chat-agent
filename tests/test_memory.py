import pytest
from libs.core.memory import memory_manager
from langchain_core.messages import HumanMessage, AIMessage

def test_memory_session_isolation():
    """세션 간 메모리가 완전히 분리되는지 테스트"""
    session_a = "session-a"
    session_b = "session-b"

    # 세션 A에 대화 추가
    history_a = memory_manager.get_session_history(session_a)
    history_a.add_message(HumanMessage(content="Hello A"))
    
    # 세션 B 확인 (비어있어야 함)
    history_b = memory_manager.get_session_history(session_b)
    assert len(history_b.messages) == 0

    # 세션 A 확인 (데이터가 있어야 함)
    assert len(history_a.messages) == 1
    assert history_a.messages[0].content == "Hello A"

def test_memory_persistence():
    """동일 세션에서 대화가 누적되는지 테스트"""
    session_id = "test-session"
    
    # 첫 번째 대화
    history = memory_manager.get_session_history(session_id)
    history.add_user_message("Hi")
    history.add_ai_message("Hello!")

    # 다시 가져왔을 때 내역 유지 확인
    new_history = memory_manager.get_session_history(session_id)
    assert len(new_history.messages) == 2
    assert isinstance(new_history.messages[1], AIMessage)

def test_memory_clear():
    """세션 삭제 기능 테스트"""
    session_id = "to-be-cleared"
    history = memory_manager.get_session_history(session_id)
    history.add_user_message("Delete me")
    
    memory_manager.clear_session(session_id)
    
    # 새로 가져오면 비어있어야 함
    new_history = memory_manager.get_session_history(session_id)
    assert len(new_history.messages) == 0
