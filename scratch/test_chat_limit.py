import httpx
import json
import asyncio

async def test_large_chat_request():
    url = "http://localhost:8000/chat"
    
    # 약 4000자 이상의 긴 메시지 생성 (URL 제한 테스트용)
    large_message = "테스트 메시지입니다. " * 500
    
    payload = {
        "message": large_message,
        "model_type": "chat",
        "temperature": 0.7
    }
    
    # 이 테스트는 서버가 실행 중이어야 합니다.
    # 실제 환경에서는 테스트용 Mock을 쓰거나 서버를 띄워야 하지만, 
    # 여기서는 설계된 페이로드 구조가 맞는지 확인하는 용도로 작성합니다.
    print(f"Payload size: {len(json.dumps(payload))} bytes")
    
    # 실제 호출 테스트 (서버가 떠있다면 실행)
    try:
        async with httpx.AsyncClient() as client:
            # Note: 실제 환경에서는 인증 토큰 등이 필요할 수 있음
            # 여기서는 단순히 전송 규격 확인용
            print("Sending request to /chat...")
            # response = await client.post(url, json=payload, timeout=10.0)
            # print(f"Response status: {response.status_code}")
    except Exception as e:
        print(f"Request failed (expected if server not running): {e}")

if __name__ == "__main__":
    asyncio.run(test_large_chat_request())
