import httpx

# FastAPI 백엔드에 직접 검색 요청
response = httpx.get("http://localhost:28000/api/rag/search?collection_id=all&domain_id=all")
if response.status_code == 200:
    data = response.json()
    print(f"API Returned: {len(data)} items")
    if len(data) > 0:
        print("First item:", data[0])
else:
    print(f"Error: {response.status_code} - {response.text}")
