import pytest
from libs.core.memory import memory_manager
from langchain_core.messages import HumanMessage, AIMessage

def test_memory_session_isolation():
    """세션 간 메모리가 완전히 분리되는지 테스트"""
    session_a = "session-a"
    session_b = "session-b"

    # 세션 A에 대화 추가
    history_a = memory_manager.get_thread_history(session_a)
    history_a.add_message(HumanMessage(content="Hello A"))
    
    # 세션 B 확인 (비어있어야 함)
    history_b = memory_manager.get_thread_history(session_b)
    assert len(history_b.messages) == 0

    # 세션 A 확인 (데이터가 있어야 함)
    assert len(history_a.messages) == 1
    assert history_a.messages[0].content == "Hello A"

def test_memory_persistence():
    """동일 세션에서 대화가 누적되는지 테스트"""
    thread_id = "test-session"
    
    # 첫 번째 대화
    history = memory_manager.get_thread_history(thread_id)
    history.add_user_message("Hi")
    history.add_ai_message("Hello!")

    # 다시 가져왔을 때 내역 유지 확인
    new_history = memory_manager.get_thread_history(thread_id)
    assert len(new_history.messages) == 2
    assert isinstance(new_history.messages[1], AIMessage)

def test_memory_clear():
    """세션 삭제 기능 테스트"""
    thread_id = "to-be-cleared"
    history = memory_manager.get_thread_history(thread_id)
    history.add_user_message("Delete me")
    
    memory_manager.clear_thread(thread_id)
    
    # 새로 가져오면 비어있어야 함
    new_history = memory_manager.get_thread_history(thread_id)
    assert len(new_history.messages) == 0

def test_memory_sliding_window():
    """메시지 개수 제한(Sliding Window) 기능 테스트"""
    thread_id = "window-test"
    from libs.core.settings import settings
    
    # 설정된 최대치보다 많이 입력 (예: 10개 제한인데 15개 입력)
    max_msgs = settings.MEMORY_MAX_MESSAGES
    history = memory_manager.get_thread_history(thread_id)
    
    for i in range(max_msgs + 5):
        history.add_user_message(f"Message {i}")
    
    # 다시 가져올 때 트리밍이 발생해야 함
    trimmed_history = memory_manager.get_thread_history(thread_id)
    
    # 1. 개수가 max_msgs를 초과하지 않아야 함
    assert len(trimmed_history.messages) <= max_msgs
    
    # 2. 가장 마지막 메시지가 살아남아 있어야 함 (가장 최근 것 유지)
    assert trimmed_history.messages[-1].content == f"Message {max_msgs + 4}"
    
    # 3. 가장 오래된 메시지(Message 0)는 삭제되어야 함
    contents = [m.content for m in trimmed_history.messages]
    assert "Message 0" not in contents
