import asyncio
from qdrant_client import QdrantClient
import json

async def inspect_qdrant():
    client = QdrantClient(url="http://localhost:6333")
    
    # 1. 모든 콜렉션 목록 조회
    collections = client.get_collections().collections
    print(f"--- Collections ---")
    for col in collections:
        name = col.name
        info = client.get_collection(name)
        
        # 인덱스 정보 추출
        payload_schema = info.payload_schema
        print(f"Collection: {name}")
        print(f"  - Payload Schema: {payload_schema}")
        
        # 2. "python" 검색 테스트 (단순 필터 없이 전체 데이터에서 확인)
        points, _ = client.scroll(
            collection_name=name,
            limit=100,
            with_payload=True
        )
        
        print(f"  - Data Sample (Check for 'python'):")
        found_count = 0
        for p in points:
            payload_str = json.dumps(p.payload).lower()
            if "python" in payload_str:
                found_count += 1
                print(f"    [Found in ID {p.id}] Payload: {p.payload}")
        
        if found_count == 0:
            print(f"    (No 'python' found in this collection)")
        else:
            print(f"    (Total {found_count} matches in sample)")

if __name__ == "__main__":
    asyncio.run(inspect_qdrant())
