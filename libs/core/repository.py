from typing import List, Type, TypeVar, Optional, Generic, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from libs.core.database import Base
from libs.core.models import Collection, Domain

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
