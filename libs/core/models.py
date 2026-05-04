from sqlalchemy import Column, String, Integer, Text, Boolean, DateTime, func
from libs.core.database import Base

class Collection(Base):
    """
    RAG 유형(Collection) 관리 모델
    - collection_name: 실제 Qdrant 콜렉션 명 (Primary Key, 수정 불가)
    - name: UI 표시용 명칭
    """
    __tablename__ = "collections"

    collection_name = Column(String(100), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    snippet_size_limit = Column(Integer, default=500)
    search_method = Column(String(50), default="vector")  # e.g., "bm25", "vector"

class Domain(Base):
    """
    지식 분야(Domain) 관리 모델
    """
    __tablename__ = "domains"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)


class Prompt(Base):
    """
    개인 시스템 프롬프트 관리 모델
    - user_id: OIDC sub (소유자 식별자)
    - is_public: 타 user에게 공개 여부
    """
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    username = Column(String(255), nullable=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    is_public = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
