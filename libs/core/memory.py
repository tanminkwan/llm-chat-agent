from typing import Dict
from langchain_core.chat_history import BaseChatMessageHistory, InMemoryChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory

class MemoryManager:
    """사용자 세션별 대화 이력을 관리하는 싱글톤 클래스"""
    _instance = None
    _histories: Dict[str, BaseChatMessageHistory] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MemoryManager, cls).__new__(cls)
        return cls._instance

    def get_session_history(self, session_id: str) -> BaseChatMessageHistory:
        """세션 ID에 해당하는 대화 이력을 반환하거나 새로 생성함"""
        if session_id not in self._histories:
            self._histories[session_id] = InMemoryChatMessageHistory()
        return self._histories[session_id]

    def clear_session(self, session_id: str):
        """특정 세션의 대화 이력을 삭제"""
        if session_id in self._histories:
            del self._histories[session_id]

# 싱글톤 인스턴스 노출
memory_manager = MemoryManager()
