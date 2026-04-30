from sqlalchemy import Column, String, Integer, Text
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
