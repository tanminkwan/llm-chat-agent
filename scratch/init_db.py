import asyncio
import os
import sys

# 프로젝트 루트를 경로에 추가
sys.path.append(os.getcwd())

from libs.core.database import engine, Base
from libs.core.models import Collection, Domain

async def init_db():
    async with engine.begin() as conn:
        print("Dropping existing tables...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Creating new tables...")
        await conn.run_sync(Base.metadata.create_all)
    print("Database initialized successfully with collection_name as Primary Key.")

if __name__ == "__main__":
    asyncio.run(init_db())
