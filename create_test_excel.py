import pandas as pd

data = [
    {
        "Content": "Docker 미사용 이미지 정리 명령어",
        "Extended Content": "시스템에 쌓인 미사용 도커 이미지와 컨테이너를 한 번에 정리하려면 'docker system prune -a' 명령어를 사용합니다. 이 작업은 디스크 공간을 확보하는 데 유용합니다."
    },
    {
        "Content": "Nginx 리버스 프록시 역할",
        "Extended Content": "Nginx는 클라이언트의 요청을 받아 백엔드 서버(FastAPI 등)로 전달하는 리버스 프록시 역할을 수행하며, SSL 터미네이션과 로드 밸런싱 기능을 제공합니다."
    },
    {
        "Content": "Git Merge와 Rebase의 차이점",
        "Extended Content": "Merge는 브랜치의 병합 기록을 커밋으로 남기는 반면, Rebase는 커밋 히스토리를 선형으로 깔끔하게 재정렬하여 마치 한 브랜치에서 작업한 것처럼 만듭니다."
    },
    {
        "Content": "Python GIL(Global Interpreter Lock)의 개념",
        "Extended Content": "Python GIL은 한 번에 하나의 스레드만 파이썬 바이트코드를 실행하도록 잠그는 뮤텍스로, 멀티스레딩 환경에서 CPU 바운드 작업의 병렬 처리를 제한합니다."
    },
    {
        "Content": "(의도적 에러 유발) 매우 긴 마이크로서비스 아키텍처 설명 " * 15,
        "Extended Content": "이 데이터는 스니펫 제한(기본 500바이트)을 초과하도록 의도적으로 만들어진 아주 긴 문자열 데이터입니다. " * 5
    },
    {
        "Content": "FastAPI BackgroundTasks의 장점",
        "Extended Content": "HTTP 요청 응답을 클라이언트에게 먼저 보낸 후, 이메일 전송이나 엑셀 파싱 등 시간이 오래 걸리는 작업을 백그라운드에서 비동기적으로 처리할 수 있게 해줍니다."
    },
    {
        "Content": "PostgreSQL EXPLAIN ANALYZE 사용법",
        "Extended Content": "쿼리의 실행 계획을 분석할 때 EXPLAIN ANALYZE를 붙이면 실제 실행 시간과 접근하는 인덱스를 보여주어 쿼리 최적화에 필수적인 정보를 제공합니다."
    },
    {
        "Content": "Qdrant 벡터 데이터베이스의 특징",
        "Extended Content": "Qdrant는 Rust 기반의 고성능 벡터 검색 엔진으로, 코사인 유사도 연산 및 메타데이터 필터링에 탁월한 성능을 보여 RAG 아키텍처에 적합합니다."
    },
    {
        "Content": "OIDC(OpenID Connect) 인증 흐름",
        "Extended Content": "OIDC는 OAuth 2.0 프로토콜 위에 인증 레이어를 추가한 표준으로, ID 토큰(JWT)을 통해 사용자의 신원 정보를 클라이언트에 안전하게 전달합니다."
    },
    {
        "Content": "JWT(JSON Web Token)의 3가지 구조",
        "Extended Content": "" # 자동 복사 테스트용 공란
    },
    {
        "Content": "Kubernetes Pod의 생명주기",
        "Extended Content": "Pod는 Pending, Running, Succeeded, Failed, Unknown의 5가지 상태를 가지며, 영구적이지 않으므로 장애 시 다른 노드에서 재시작됩니다."
    },
    {
        "Content": "SOLID 객체 지향 설계 원칙",
        "Extended Content": "단일 책임(SRP), 개방-폐쇄(OCP), 리스코프 치환(LSP), 인터페이스 분리(ISP), 의존관계 역전(DIP)의 앞 글자를 딴 객체 지향의 핵심 원칙입니다."
    },
    {
        "Content": "RESTful API 설계 모범 사례",
        "Extended Content": "URI는 동사가 아닌 명사를 사용해야 하며, HTTP 메서드(GET, POST, PUT, DELETE)를 통해 행위를 정의하고 적절한 상태 코드(2xx, 4xx)를 반환해야 합니다."
    },
    {
        "Content": "GraphQL과 REST의 차이점",
        "Extended Content": "GraphQL은 클라이언트가 필요한 데이터만 쿼리할 수 있어 오버패칭과 언더패칭 문제를 해결할 수 있는 단일 엔드포인트 기반의 데이터 질의 언어입니다."
    },
    {
        "Content": "SSH Key 생성 및 접속 방법",
        "Extended Content": "ssh-keygen 명령어로 공개키와 개인키 쌍을 생성한 후, 공개키를 서버의 authorized_keys에 등록하면 패스워드 없이 안전하게 접속할 수 있습니다."
    },
    {
        "Content": "Docker Compose 네트워크 구성",
        "Extended Content": "Docker Compose는 기본적으로 동일한 브리지 네트워크를 생성하여 각 서비스 간에 컨테이너 이름을 도메인처럼 사용하여 통신할 수 있게 합니다."
    },
    {
        "Content": "Python AsyncIO의 비동기 I/O 동작 원리",
        "Extended Content": "Event Loop를 통해 단일 스레드 내에서 I/O 대기 시간 동안 다른 작업을 수행하도록 컨텍스트를 스위칭하여 네트워크 요청 성능을 극대화합니다."
    },
    {
        "Content": "Pydantic을 활용한 데이터 검증",
        "Extended Content": "FastAPI에서 Pydantic 모델을 사용하면 들어오는 JSON 페이로드의 타입을 자동으로 캐스팅하고 필드 제약 조건을 강력하게 검증해 줍니다."
    },
    {
        "Content": "SQL B-Tree 인덱스의 장점",
        "Extended Content": "B-Tree 인덱스는 데이터를 트리의 리프 노드에 정렬된 상태로 유지하여 동등(=) 조건 및 범위(<, >) 검색 속도를 획기적으로 향상시킵니다."
    },
    {
        "Content": "RAG(Retrieval-Augmented Generation) 아키텍처 개요",
        "Extended Content": "RAG는 환각(Hallucination) 현상을 줄이기 위해 LLM이 답변을 생성하기 전, 외부 지식 베이스(벡터 DB)에서 관련 문서를 먼저 검색하여 컨텍스트로 제공하는 기술입니다."
    }
]

df = pd.DataFrame(data)
df.to_excel("test_20.xlsx", index=False)
print("test_20.xlsx 파일이 의미있는 IT/개발 지식 내용으로 새롭게 생성되었습니다.")
