# 리발소 (LLM Chat Agent)

OIDC 인증 기반의 사내용 LLM 채팅·RAG 에이전트. 사용자별 채팅 메모리·RAG 검색·관리자 콘솔·관측성(Loki/Grafana)을 단일 docker compose 스택으로 운영한다.

> "리발소" = "이발소" 톤의 비공식 코드네임 (`APP_NAME` 환경변수).

---

## 주요 기능

- **OIDC SSO 로그인** (외부 mwm-idp 클라이언트, HTTPS 종단)
- **다중 모델 LLM 호출** — `CHAT` / `REASONING` / `EMBEDDING` 3개 슬롯, OpenAI·vLLM 호환 API 자동 분기
- **대화 메모리 (thread 기반)** — 사용자·thread 단위 메시지 보존, `MEMORY_MAX_MESSAGES` 슬라이딩 윈도
- **RAG** — Qdrant 벡터 스토어 + PostgreSQL 메타데이터, 컬렉션·도메인 CRUD, 벡터·키워드·하이브리드 검색
- **개인 Prompt 관리** (Phase 05.1) — 본인 시스템 프롬프트 저장·재사용, 동료가 공개한 프롬프트 조회
- **SPA UI** — 4개 화면(`/`, `/rag`, `/bulk`, `/admin`)을 단일 페이지로 통합해 화면 전환 시 작업 상태 보존
- **운영 가시성** (Phase 06) — `[LLM_LOG]` JSON 라인을 Promtail→Loki→Grafana 로 자동 수집·시각화. Admin/User 폴더 분리 + 드릴다운 data link

---

## 스택

| 계층 | 컴포넌트 |
|---|---|
| Reverse proxy | Nginx (TLS 종단, sub-path 라우팅) |
| App | FastAPI + uvicorn, LangChain (`langchain-openai`, `langchain-community`) |
| LLM | OpenAI 또는 vLLM (OpenAI 호환 엔드포인트) |
| Vector store | Qdrant |
| RDBMS | PostgreSQL 16 (asyncpg + SQLAlchemy 2.x) |
| Auth (IDP) | mwm-idp (OIDC) — 외부 호스트 |
| Observability | Loki, Promtail, Grafana |

---

## 디렉토리 구조

```
.
├── apps/api/             # FastAPI 라우터·정적 자산 (SPA)
├── libs/core/            # 인증·DB·LLM·메모리·서비스 계층
├── docs/                 # Phase 별 요구사항·설계서·테스트결과서 (한국어)
├── ops/
│   ├── grafana/
│   │   ├── dashboards/
│   │   │   ├── admin/    # 운영 개요·RAG 품질·드릴다운(Top 20 포함)
│   │   │   └── user/     # 본인 LLM_LOG 자동 필터 (${__user.login})
│   │   └── provisioning/ # datasource/dashboard provider 분리
│   ├── loki/             # Loki 단일 인스턴스 설정
│   └── promtail/         # 컨테이너 로그 → [LLM_LOG] 라인만 파싱·라벨링
├── tests/
├── nginx.conf
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── sample.env            # → .env 로 복사해 채워 사용
```

---

## 빠른 시작

```bash
# 1) 환경 변수 채우기
cp sample.env .env
$EDITOR .env  # OIDC, LLM API 키, GRAFANA_ADMIN_PASSWORD 등

# 2) (선택) host alias 설정 — TLS SAN/OIDC redirect 가 도메인을 요구
sudo tee -a /etc/hosts <<'EOF'
127.0.0.1 llm-agent.mwm.local
127.0.0.1 idp.mwm.local
EOF

# 3) 컨테이너 빌드 및 기동
docker compose build llm-agent
docker compose up -d
```

기동 후:

| URL | 용도 |
|---|---|
| `https://llm-agent.mwm.local:21443/` | 채팅 SPA (OIDC 로그인 필요) |
| `https://llm-agent.mwm.local:21443/grafana/` | Grafana (admin / `GRAFANA_ADMIN_PASSWORD`) |
| `https://llm-agent.mwm.local:21443/docs` | FastAPI Swagger UI (오프라인 자산) |

---

## 관측성 (Phase 06)

Promtail 이 `llm-agent` 컨테이너의 stdout 에서 `[LLM_LOG] {json}` 라인만 추려 Loki 로 적재한다. 라인 본문은 Python logging prefix 가 제거된 순수 JSON 으로 저장되며, 라벨/메타데이터 분리는 다음과 같다.

- **저카디널리티 라벨 (인덱스):** `container, type, model_type, search_method`
- **Structured metadata:** `request_id, thread_id, user_id, model`

Grafana 대시보드는 폴더로 분리되어 있다:

| 폴더 | 대시보드 | 대상 |
|---|---|---|
| `LLM Admin` | LLM 운영 개요 | 모델별 호출 수·응답 지연·에러율·토큰 사용량 |
| `LLM Admin` | LLM RAG 품질 | 검색 알고리즘 분포·zero-result 비율·평균 top_score |
| `LLM Admin` | LLM 사용자 / 트레이스 드릴다운 | Top 20 사용자 + user_id 셀 클릭 → 같은 dashboard 자동 필터 (data link) |
| `LLM User` | 내 LLM 사용 현황 | `${__user.login}` 자동 바인딩 — 본인 데이터만 노출 |

드릴다운 패널 쿼리는 `| json` 없이 structured metadata 직접 비교 형태로 단순화되어 있다:

```logql
{container="llm-agent"} | user_id = `$user_id`
{container="llm-agent"} | request_id = `$request_id`
```

OIDC SSO 는 Phase 06.1 에서 활성화 예정이며, 그 전까지는 임시 admin 계정 한 개로 접속한다 (`docker-compose.yml` 의 `GF_AUTH_GENERIC_OAUTH_*` 블록은 주석 처리되어 있다).

---

## Phase 진행 상황

| Phase | 주제 | 상태 |
|---|---|---|
| P00 | 프로젝트 개요·요구사항 정의 | ✅ |
| P01 | OIDC 인증·HTTPS 인프라 | ✅ |
| P02 | 핵심 모듈 (LLM·메모리·Qdrant) | ✅ |
| P03 | PostgreSQL 도입·Admin CRUD | ✅ |
| P04 | RAG 통합 관리 콘솔 (전체 화면) | ✅ |
| P04.1 | 커스텀 Embedding API 지원 | ✅ |
| P04.2 | 오프라인 Swagger UI | ✅ |
| P04.3 | Thread 개념·로깅 개선 | ✅ |
| P05 | SPA 전환 | ✅ |
| P05.1 | 개인 Prompt 관리 | ✅ |
| P06 | Loki/Promtail/Grafana 관측성 | ✅ |
| P06.1 | Grafana OIDC SSO 연동 | ✅ |
| P07 | Tool Lab — 동적 도구 등록 + LLM Tool Calling 시뮬레이터 | 진행 중 |

각 Phase 의 요구사항·설계서·테스트결과서는 [`docs/`](docs/) 에 있다.

---

## 자주 보는 문서

- [`docs/P00_요구사항.md`](docs/P00_요구사항.md) — 전체 프로젝트 요구사항
- [`docs/IDP_설정가이드.md`](docs/IDP_설정가이드.md) — mwm-idp OIDC 클라이언트 등록 절차
- [`docs/배포가이드.md`](docs/배포가이드.md) — 운영 배포 체크리스트
- [`docs/권한관리설계서.md`](docs/권한관리설계서.md) — 사용자/관리자 권한 모델
- [`docs/P06_설계서.md`](docs/P06_설계서.md) — 관측성 스택 상세
- [`docs/P07_요구사항.md`](docs/P07_요구사항.md) — Tool Lab 요구사항
- [`docs/P07_설계서.md`](docs/P07_설계서.md) — Tool Lab 설계서
- [`docs/P07_vllm_tool_calling_검증.md`](docs/P07_vllm_tool_calling_검증.md) — vLLM tool calling 사전 검증 결과

---

## 라이선스

Internal use only.
