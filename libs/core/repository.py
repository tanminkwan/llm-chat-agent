from typing import List, Type, TypeVar, Optional, Generic, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_
from libs.core.database import Base
from libs.core.models import Collection, Domain, Prompt

T = TypeVar("T", bound=Base)

class BaseRepository(Generic[T]):
    def __init__(self, model: Type[T], db: AsyncSession):
        self.model = model
        self.db = db

    async def get_all(self) -> List[T]:
        result = await self.db.execute(select(self.model))
        return result.scalars().all()

    async def get_by_id(self, obj_id: Any) -> Optional[T]:
        return await self.db.get(self.model, obj_id)

    async def create(self, **kwargs) -> T:
        obj = self.model(**kwargs)
        self.db.add(obj)
        await self.db.commit()
        await self.db.refresh(obj)
        return obj

    async def update(self, obj: T, **kwargs) -> T:
        for key, value in kwargs.items():
            setattr(obj, key, value)
        await self.db.commit()
        await self.db.refresh(obj)
        return obj

    async def delete(self, obj: T):
        await self.db.delete(obj)
        await self.db.commit()

class CollectionRepository(BaseRepository[Collection]):
    def __init__(self, db: AsyncSession):
        super().__init__(Collection, db)

class DomainRepository(BaseRepository[Domain]):
    def __init__(self, db: AsyncSession):
        super().__init__(Domain, db)


class PromptRepository(BaseRepository[Prompt]):
    def __init__(self, db: AsyncSession):
        super().__init__(Prompt, db)

    async def search(
        self,
        user_id: str,
        include_others: bool = False,
        title_keyword: Optional[str] = None,
    ) -> List[Prompt]:
        """
        조회 규칙:
        - 본인 것은 항상 조회 가능
        - include_others=True 면 타 user 의 공개(is_public=True) 건도 포함
        - title_keyword 가 있으면 제목에 부분 일치(ILIKE) 필터 추가
        """
        stmt = select(Prompt)

        if include_others:
            stmt = stmt.where(
                or_(
                    Prompt.user_id == user_id,
                    Prompt.is_public == True,
                )
            )
        else:
            stmt = stmt.where(Prompt.user_id == user_id)

        if title_keyword:
            stmt = stmt.where(Prompt.title.ilike(f"%{title_keyword}%"))

        stmt = stmt.order_by(Prompt.updated_at.desc())
        result = await self.db.execute(stmt)
        return result.scalars().all()
