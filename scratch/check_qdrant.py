from qdrant_client import QdrantClient
import os
from dotenv import load_dotenv

load_dotenv()
qdrant_url = "http://localhost:6333"

try:
    client = QdrantClient(url=qdrant_url)
    collections = client.get_collections().collections
    print(f"Collections: {[c.name for c in collections]}")
    
    if "test" in [c.name for c in collections]:
        count = client.count(collection_name="test")
        print(f"Total points in 'test': {count.count}")
        
        # 실제 scroll 호출로 몇 개 나오는지 확인
        res, _ = client.scroll(collection_name="test", limit=50)
        print(f"Points returned by scroll (limit 50): {len(res)}")
    else:
        print("Collection 'test' not found.")
except Exception as e:
    print(f"Error: {e}")
