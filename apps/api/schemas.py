from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# --- Collection Schemas ---

class CollectionBase(BaseModel):
    name: str = Field(..., description="UI에 표시될 콜렉션의 별칭")
    description: Optional[str] = Field(None, description="콜렉션에 대한 상세 설명")
    snippet_size_limit: int = Field(500, description="텍스트 조각(스니펫)의 최대 바이트 크기")
    search_method: str = Field("vector", description="기본 검색 방식 (vector 또는 text_matching)")

class CollectionCreate(CollectionBase):
    collection_name: str = Field(..., pattern="^[a-zA-Z0-9_-]+$", description="시스템 내부에서 사용할 고유 ID (영문, 숫자, -, _ 만 허용)")

class CollectionRead(CollectionBase):
    collection_name: str = Field(..., description="콜렉션 고유 ID")
    
    class Config:
        from_attributes = True

# --- Domain Schemas ---

class DomainCreate(BaseModel):
    name: str = Field(..., description="도메인(분류) 명칭")

class DomainRead(DomainCreate):
    id: int = Field(..., description="도메인 고유 번호")
    
    class Config:
        from_attributes = True

# --- Knowledge & Search Schemas ---

class KnowledgeCreate(BaseModel):
    collection_name: str = Field(..., description="지식을 추가할 대상 콜렉션 ID")
    domain_id: int = Field(..., description="지식이 속한 도메인(분류) ID")
    content: str = Field(..., description="검색 대상이 되는 텍스트 내용")
    extended_content: str = Field(..., description="검색 결과 클릭 시 보여줄 상세 원문 내용")
    source: str = Field(..., description="출처 정보 (파일명, URL 등)")
    point_id: Optional[str] = Field(None, description="기존 지식 수정 시 사용되는 고유 ID")

class SearchResult(BaseModel):
    id: str = Field(..., description="지식 데이터 고유 ID")
    collection: str = Field(..., description="해당 데이터가 속한 콜렉션 ID")
    score: float = Field(..., description="검색 유사도 점수 (텍스트 매칭 시 1.0, 벡터 검색 시 0~1)")
    content: str = Field(..., description="검색된 지식 내용")
    extended_content: str = Field(..., description="상세 원문 내용")
    domain_id: int = Field(..., description="도메인 ID")
    source: str = Field(..., description="출처 정보")
    created_at: str = Field(..., description="데이터 등록 일시 (ISO 8601)")

class SearchRequest(BaseModel):
    collection_id: Optional[str] = Field(None, description="검색할 콜렉션 ID")
    domain_id: Optional[int] = Field(None, description="특정 도메인 필터링")
    query: Optional[str] = Field(None, description="검색어")
    search_method: str = Field("vector", description="검색 알고리즘 (vector, text_matching)")
    limit: int = Field(10, description="최대 검색 결과 수")

# --- System & Auth Schemas ---

class UserInfo(BaseModel):
    sub: str = Field(..., description="사용자 고유 식별자")
    preferred_username: str = Field(..., description="사용자 표시 이름")
    groups: List[str] = Field(..., description="소속 그룹 목록")
    
    @property
    def is_admin(self) -> bool:
        return "Admin" in self.groups

class ConfigResponse(BaseModel):
    app_name: str
    chat_model: str
    chat_label: str
    reasoning_model: str
    reasoning_label: str
    grafana_url: Optional[str] = None

class MessageResponse(BaseModel):
    message: str = Field(..., description="결과 메시지")

class TaskStatusResponse(BaseModel):
    total: int = Field(..., description="전체 처리 대상 건수")
    success: int = Field(..., description="성공 건수")
    error: int = Field(..., description="실패 건수")
    done: bool = Field(..., description="작업 완료 여부")
    error_file: Optional[str] = Field(None, description="실패 내역 엑셀 파일 경로 (완료 후 존재 시)")

class DeleteCountResponse(BaseModel):
    count: int = Field(..., description="삭제 대상 데이터 개수")

# --- Prompt Schemas ---

class PromptBase(BaseModel):
    title: str = Field(..., max_length=200, description="프롬프트 제목")
    content: str = Field(..., description="시스템 프롬프트 본문")
    is_public: bool = Field(True, description="타 user에게 공개 여부 (기본 공개)")

class PromptCreate(PromptBase):
    pass

class PromptUpdate(PromptBase):
    pass

class PromptRead(PromptBase):
    id: int = Field(..., description="프롬프트 고유 번호")
    user_id: str = Field(..., description="소유자 ID (OIDC sub)")
    username: Optional[str] = Field(None, description="소유자 표시 이름")
    is_owner: bool = Field(False, description="현재 사용자가 소유자인지 여부")
    created_at: datetime = Field(..., description="생성일시")
    updated_at: datetime = Field(..., description="변경일시")

    class Config:
        from_attributes = True

class ChatRequest(BaseModel):
    message: str = Field(..., description="사용자 메시지")
    model_type: str = Field("chat", description="모델 타입 (chat: 일반, reasoning: 사고형/추론형)")
    system_prompt: Optional[str] = Field(None, description="시스템 프롬프트")
    temperature: Optional[float] = Field(0.7, description="창의성 조절 (0~1)")
    thread_id: Optional[str] = Field(None, description="대화 쓰레드 ID")
