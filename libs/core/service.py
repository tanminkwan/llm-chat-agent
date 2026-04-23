from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from libs.core.repository import CollectionRepository, DomainRepository
from libs.core.models import Collection, Domain
from libs.core.settings import settings
from qdrant_client import QdrantClient

class RAGService:
    def __init__(self, db: AsyncSession):
        self.col_repo = CollectionRepository(db)
        self.dom_repo = DomainRepository(db)
        self.qdrant = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)

    # --- Collection Methods ---
    async def list_collections(self) -> List[Collection]:
        return await self.col_repo.get_all()

    async def create_collection(self, collection_name: str, name: str, description: Optional[str], snippet_size_limit: int, search_method: str) -> Collection:
        """콜렉션 생성 (Qdrant 연동 고려 가능)"""
        return await self.col_repo.create(
            collection_name=collection_name,
            name=name,
            description=description,
            snippet_size_limit=snippet_size_limit,
            search_method=search_method
        )

    async def update_collection(self, collection_name: str, **kwargs) -> Collection:
        """콜렉션 정보 수정 (collection_name은 수정 불가)"""
        col = await self.col_repo.get_by_id(collection_name)
        if not col:
            raise ValueError("Collection not found")
        
        # 식별자인 collection_name은 수정에서 제외
        if "collection_name" in kwargs:
            del kwargs["collection_name"]
            
        return await self.col_repo.update(col, **kwargs)

    async def delete_collection(self, collection_name: str, delete_vector: bool = False):
        """콜렉션 삭제 및 벡터 DB 데이터 삭제 선택"""
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
        await self.dom_repo.delete(dom)
