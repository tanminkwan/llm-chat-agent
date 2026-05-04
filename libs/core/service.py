import uuid
import datetime
from typing import List, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from libs.core.repository import CollectionRepository, DomainRepository, PromptRepository
from libs.core.models import Collection, Domain, Prompt
from libs.core.settings import settings
from libs.core.llm import LLMGateway
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

class RAGService:
    def __init__(self, db: AsyncSession):
        self.col_repo = CollectionRepository(db)
        self.dom_repo = DomainRepository(db)
        self.qdrant = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
        self.embeddings = LLMGateway.get_embeddings()

    # --- Collection Methods ---
    async def list_collections(self) -> List[Collection]:
        return await self.col_repo.get_all()

    async def create_collection(self, collection_name: str, name: str, description: Optional[str], snippet_size_limit: int, search_method: str) -> Collection:
        """콜렉션 생성 및 Qdrant 콜렉션 물리적 생성"""
        # DB 저장
        col = await self.col_repo.create(
            collection_name=collection_name,
            name=name,
            description=description,
            snippet_size_limit=snippet_size_limit,
            search_method=search_method
        )
        
        # Qdrant 콜렉션 생성 (존재하지 않을 경우)
        if not self.qdrant.collection_exists(collection_name):
            self.qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(size=settings.EMBEDDING_DIM, distance=qmodels.Distance.COSINE)
            )
            # domain_id 인덱스 생성
            self.qdrant.create_payload_index(collection_name, "domain_id", qmodels.PayloadSchemaType.KEYWORD)
            # created_at 인덱스 생성 (정렬 성능 향상)
            self.qdrant.create_payload_index(collection_name, "created_at", qmodels.PayloadSchemaType.KEYWORD)
            # content 필드 텍스트 인덱스 생성 (BM25 키워드 검색용)
            self.qdrant.create_payload_index(collection_name, "content", qmodels.PayloadSchemaType.TEXT)
            
        return col

    async def update_collection(self, collection_name: str, **kwargs) -> Collection:
        """콜렉션 정보 수정"""
        col = await self.col_repo.get_by_id(collection_name)
        if not col:
            raise ValueError("Collection not found")
        
        if "collection_name" in kwargs:
            del kwargs["collection_name"]
            
        return await self.col_repo.update(col, **kwargs)

    async def delete_collection(self, collection_name: str, delete_vector: bool = False):
        """콜렉션 삭제"""
        col = await self.col_repo.get_by_id(collection_name)
        if not col:
            raise ValueError("Collection not found")

        if delete_vector:
            if self.qdrant.collection_exists(col.collection_name):
                self.qdrant.delete_collection(col.collection_name)

        await self.col_repo.delete(col)

    # --- Domain Methods ---
    async def list_domains(self) -> List[Domain]:
        return await self.dom_repo.get_all()

    async def create_domain(self, name: str) -> Domain:
        return await self.dom_repo.create(name=name)

    async def update_domain(self, dom_id: int, name: str) -> Domain:
        dom = await self.dom_repo.get_by_id(dom_id)
        if not dom:
            raise ValueError("Domain not found")
        return await self.dom_repo.update(dom, name=name)

    async def delete_domain(self, dom_id: int):
        dom = await self.dom_repo.get_by_id(dom_id)
        if not dom:
            raise ValueError("Domain not found")
            
        # 1. 모든 콜렉션 조회
        collections = await self.list_collections()
        
        # 2. 각 콜렉션의 Qdrant에서 해당 domain_id를 가진 지식(포인트) 일괄 삭제
        for col in collections:
            if self.qdrant.collection_exists(col.collection_name):
                self.qdrant.delete(
                    collection_name=col.collection_name,
                    points_selector=qmodels.FilterSelector(
                        filter=qmodels.Filter(
                            must=[
                                qmodels.FieldCondition(
                                    key="domain_id",
                                    match=qmodels.MatchValue(value=dom_id)
                                )
                            ]
                        )
                    )
                )

        # 3. DB에서 도메인 메타데이터 삭제
        await self.dom_repo.delete(dom)

    # --- RAG Search & Knowledge Methods ---

    async def search_rag(self, 
                       collection_id: Optional[str] = None, 
                       domain_id: Optional[int] = None, 
                       query: Optional[str] = None, 
                       search_method: str = "vector",
                       limit: int = 20) -> List[dict]:
        """통합 RAG 검색 (필터 및 알고리즘 적용)"""
        
        # 1. 대상 콜렉션 결정
        target_collections = []
        if collection_id and collection_id != "all":
            target_collections = [collection_id]
        else:
            cols = await self.list_collections()
            target_collections = [c.collection_name for c in cols]

        # 2. 필터 구성
        must_filters = []
        if domain_id:
            must_filters.append(qmodels.FieldCondition(key="domain_id", match=qmodels.MatchValue(value=domain_id)))
        
        filter_obj = qmodels.Filter(must=must_filters) if must_filters else None

        results = []
        
        # 3. 각 콜렉션 검색
        for col_name in target_collections:
            if not self.qdrant.collection_exists(col_name):
                continue

            if query:
                if search_method == "text_matching":
                    # [Text Matching Search] - Match Text 필터 사용
                    # 벡터 검색 대신 텍스트 매칭 필터를 사용합니다.
                    text_filter = qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(key="content", match=qmodels.MatchText(text=query))
                        ] + must_filters
                    )
                    scroll_result, _ = self.qdrant.scroll(
                        collection_name=col_name,
                        scroll_filter=text_filter,
                        limit=limit,
                        with_payload=True,
                        with_vectors=False
                    )
                    for point in scroll_result:
                        results.append({
                            "id": point.id,
                            "collection": col_name,
                            "score": 1.0, # 텍스트 매칭은 기본 1.0점 처리
                            **point.payload
                        })
                else:
                    # [Vector Search] - 임베딩 기반 유사도 검색
                    query_vector = self.embeddings.embed_query(query)
                    search_result = self.qdrant.query_points(
                        collection_name=col_name,
                        query=query_vector,
                        query_filter=filter_obj,
                        limit=limit
                    ).points
                    for hit in search_result:
                        results.append({
                            "id": hit.id,
                            "collection": col_name,
                            "score": hit.score,
                            **hit.payload
                        })
            else:
                # 쿼리 없을 경우 단순 스크롤 (전체 리스트 조회용)
                # Qdrant의 scroll은 기본적으로 내부 순서지만, 페이로드에 created_at이 있다면 이후 정렬
                scroll_result, _ = self.qdrant.scroll(
                    collection_name=col_name,
                    scroll_filter=filter_obj,
                    limit=limit,
                    with_payload=True,
                    with_vectors=False
                )
                for point in scroll_result:
                    results.append({
                        "id": point.id,
                        "collection": col_name,
                        "score": 0.0, # 쿼리 없을 때 점수는 0
                        **point.payload
                    })

        # 4. 정렬 로직 분기
        if query:
            # 쿼리 있을 때: 유사도 점수(score) 내림차순
            results.sort(key=lambda x: x["score"], reverse=True)
        else:
            # 쿼리 없을 때: 등록 일시(created_at) 내림차순
            results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            
        return results[:limit]

    async def add_knowledge_point(self, 
                                collection_name: str, 
                                domain_id: int, 
                                content: str, 
                                extended_content: str, 
                                source: str,
                                point_id: Optional[str] = None):
        """지식 데이터 등록 및 수정 (point_id가 있으면 업데이트)"""
        if not self.qdrant.collection_exists(collection_name):
            raise ValueError(f"Collection {collection_name} does not exist")

        # 임베딩 생성
        vector = self.embeddings.embed_query(content)
        
        # ID 결정
        final_id = point_id if point_id else str(uuid.uuid4())
        
        # 현재 시간 추가 (등록 순 정렬용)
        # 이미 존재하는 데이터 수정 시에는 기존 created_at 유지 고려 가능하나, 여기서는 갱신 시점으로 처리
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Qdrant 저장 (Upsert)
        self.qdrant.upsert(
            collection_name=collection_name,
            points=[
                qmodels.PointStruct(
                    id=final_id,
                    vector=vector,
                    payload={
                        "content": content,
                        "extended_content": extended_content,
                        "domain_id": domain_id,
                        "source": source,
                        "created_at": created_at
                    }
                )
            ]
        )
        return {"id": final_id, "status": "success", "created_at": created_at}

    async def delete_knowledge_point(self, collection_name: str, point_id: str):
        """지식 데이터 삭제"""
        self.qdrant.delete(
            collection_name=collection_name,
            points_selector=qmodels.PointIdsList(points=[point_id])
        )
        return {"status": "success"}

    async def count_knowledge_points(self, collection_name: str, domain_id: Optional[int] = None, source: Optional[str] = None) -> int:
        """조건에 맞는 지식 데이터 개수 조회"""
        must_filters = []
        if domain_id:
            must_filters.append(qmodels.FieldCondition(key="domain_id", match=qmodels.MatchValue(value=domain_id)))
        if source:
            must_filters.append(qmodels.FieldCondition(key="source", match=qmodels.MatchValue(value=source)))
        
        filter_obj = qmodels.Filter(must=must_filters) if must_filters else None
        
        res = self.qdrant.count(
            collection_name=collection_name,
            count_filter=filter_obj,
            exact=True
        )
        return res.count

    async def bulk_delete_knowledge_points(self, collection_name: str, domain_id: Optional[int] = None, source: Optional[str] = None):
        """조건에 맞는 지식 데이터 일괄 삭제"""
        must_filters = []
        if domain_id:
            must_filters.append(qmodels.FieldCondition(key="domain_id", match=qmodels.MatchValue(value=domain_id)))
        if source:
            must_filters.append(qmodels.FieldCondition(key="source", match=qmodels.MatchValue(value=source)))
        
        if not must_filters:
            raise ValueError("At least one filter (domain_id or source) is required for bulk delete")

        filter_obj = qmodels.Filter(must=must_filters)
        
        self.qdrant.delete(
            collection_name=collection_name,
            points_selector=qmodels.FilterSelector(filter=filter_obj)
        )
        return {"status": "success"}


class PromptService:
    """개인 시스템 프롬프트 CRUD 서비스"""

    def __init__(self, db: AsyncSession):
        self.repo = PromptRepository(db)

    async def list_prompts(
        self,
        user_id: str,
        include_others: bool = False,
        title_keyword: Optional[str] = None,
    ) -> List[Prompt]:
        return await self.repo.search(
            user_id=user_id,
            include_others=include_others,
            title_keyword=title_keyword,
        )

    async def get_prompt(self, prompt_id: int, user_id: str) -> Prompt:
        prompt = await self.repo.get_by_id(prompt_id)
        if not prompt:
            raise ValueError("Prompt not found")
        if prompt.user_id != user_id and not prompt.is_public:
            raise PermissionError("조회 권한이 없습니다.")
        return prompt

    async def create_prompt(
        self,
        user_id: str,
        username: Optional[str],
        title: str,
        content: str,
        is_public: bool = True,
    ) -> Prompt:
        return await self.repo.create(
            user_id=user_id,
            username=username,
            title=title,
            content=content,
            is_public=is_public,
        )

    async def update_prompt(
        self,
        prompt_id: int,
        user_id: str,
        title: str,
        content: str,
        is_public: bool,
    ) -> Prompt:
        prompt = await self.repo.get_by_id(prompt_id)
        if not prompt:
            raise ValueError("Prompt not found")
        if prompt.user_id != user_id:
            raise PermissionError("수정 권한이 없습니다.")
        return await self.repo.update(
            prompt,
            title=title,
            content=content,
            is_public=is_public,
        )

    async def delete_prompt(self, prompt_id: int, user_id: str):
        prompt = await self.repo.get_by_id(prompt_id)
        if not prompt:
            raise ValueError("Prompt not found")
        if prompt.user_id != user_id:
            raise PermissionError("삭제 권한이 없습니다.")
        await self.repo.delete(prompt)
