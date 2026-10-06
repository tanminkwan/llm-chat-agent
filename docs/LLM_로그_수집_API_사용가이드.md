# LLM 로그 수집 API 사용 가이드

여러 가상 사용자(virtual user)로 aipro-plus 채팅 API 를 호출하고, Loki HTTP API 를 1분 주기로 polling 하여 LLM 로그를 수집하기 위한 연동 가이드입니다. 모든 예시는 Linux `curl` (+ JSON 가공용 `jq`) 기준입니다.

**대상 독자**

| 독자 | 참고 절 |
|---|---|
| 채팅 API 를 호출하는 개발자 | §1, §2 |
| 로그 수집기(poller)를 구현하는 개발자 | §1, §3, §4, §5 |
| 통계를 조회하는 개발자 | §3, §6 |

로그 수집기는 별도 프로젝트에서 구현합니다. 이 문서는 수집기가 지켜야 할 **Loki API 계약, 로그 데이터 명세, 수집 알고리즘**을 정의합니다. 문서의 Loki 동작은 모두 실제 서버(Loki 3.7.8)에서 확인한 내용입니다.

---

## 1. 개요

```
 API 사용자                         aipro-plus 서버
┌──────────────┐  POST /api/chat/sync   ┌───────────┐ stdout  ┌───────┐ push ┌──────┐
│ 채팅 클라이언트 │ ─────────────────────▶ │ llm-agent │ ──────▶ │ Alloy │ ───▶ │ Loki │
│ (X-Virtual-User)│ ◀───── 응답 JSON ───── └───────────┘ [LLM_LOG]└───────┘      └──┬───┘
├──────────────┤                                                                │
│ 로그 수집기     │ ◀──────── GET /loki/api/v1/query_range (1분 주기 polling) ──────┘
│ (별도 프로젝트)  │
└──────────────┘
```

- **채팅**: `POST /api/chat/sync` 요청 시 `X-Virtual-User` 헤더로 가상 사용자를 지정합니다. 응답에 로그 추적용 `request_id` 가 포함됩니다.
- **로그 기록**: llm-agent 가 요청마다 `[LLM_LOG]` JSON 을 stdout 에 출력하고, Alloy 가 이를 Loki 로 전송합니다. 모든 로그에 `virtual_user` 필드가 포함됩니다.
- **로그 수집**: 수집기가 Loki HTTP API 를 직접 호출합니다. 정상 상태에서 로그는 생성 후 약 1초 이내에 조회 가능합니다.

### 1.1 접속 정보

| 항목 | 주소 | 인증 |
|---|---|---|
| aipro-plus API | `http://<서버IP>:28000` | 없음 (비 로그인 모드) |
| Loki HTTP API | `http://<서버IP>:3100` | 없음 |
| Alloy 상태 확인 | `http://<서버IP>:12345/-/ready` | 없음 |

이하 예시는 아래 환경 변수를 사용합니다. (현재 서버 IP: `10.10.20.11`)

```bash
export AIPRO_URL=http://10.10.20.11:28000
export LOKI_URL=http://10.10.20.11:3100
```

> **프록시 환경 주의**: 셸에 `http_proxy` / `https_proxy` 가 설정되어 있고 서버 IP 가 `no_proxy` 에 없으면 요청이 프록시로 나가 실패할 수 있습니다. 이 경우 `curl` 에 `--noproxy '*'` 를 붙이거나 `no_proxy` 에 서버 IP 를 추가하세요.

---

## 2. 채팅 API — `POST /api/chat/sync`

### 2.1 요청

**헤더**

| 헤더 | 필수 | 설명 |
|---|---|---|
| `Content-Type: application/json` | O | |
| `X-Virtual-User` | 권장 | 가상 사용자 ID. 형식 `[A-Za-z0-9._@-]` 1~64자. 생략 시 `nobody` 로 기록 |

**Body (JSON)**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `message` | string | O | | 사용자 메시지 |
| `thread_id` | string | | `user_<virtual_user>` | 대화 쓰레드 ID (이전 대화 맥락 유지 단위). §2.4 참고 |
| `model_type` | string | | `chat` | `chat` (Qwen/Qwen3.8-27B) 또는 `reasoning` (openai/gpt-oss-120b) |
| `system_prompt` | string | | `당신은 AI 어시스턴트입니다.` | 시스템 프롬프트 |
| `temperature` | number | | `0.7` | 0~1. `reasoning` 모델에는 적용되지 않음 |

**예시**

```bash
curl -s -X POST "$AIPRO_URL/api/chat/sync" \
  -H "Content-Type: application/json" \
  -H "X-Virtual-User: alice" \
  -d '{
        "message": "대한민국의 수도는?",
        "thread_id": "alice-session-001",
        "model_type": "chat"
      }'
```

### 2.2 응답

**200 OK**

```http
HTTP/1.1 200 OK
content-type: application/json
x-request-id: b641bbdd
```

```json
{
  "content": "대한민국의 수도는 서울입니다.",
  "usage": {
    "input_tokens": 64,
    "output_tokens": 12,
    "model": "Qwen/Qwen3.8-27B"
  },
  "request_id": "b641bbdd"
}
```

- `request_id` 는 이 요청의 로그(§3)에 기록된 `request_id` 와 같은 값입니다. 응답 헤더 `X-Request-Id` 로도 반환됩니다.
- `500` 오류 응답에도 `X-Request-Id` 헤더가 포함되므로 실패한 요청의 `error` 로그를 찾을 수 있습니다.

**오류**

| 상태 | 원인 | 로그 기록 |
|---|---|---|
| `400` | `X-Virtual-User` 형식 오류 — `{"detail":"X-Virtual-User must match [A-Za-z0-9._@-]{1,64}"}` | 없음 |
| `422` | Body 검증 실패 (예: `message` 누락) | 없음 |
| `500` | LLM 호출 실패 — `{"detail":"<오류 메시지>"}`. `X-Request-Id` 헤더 포함 | `request` + `error` |

### 2.3 여러 가상 사용자로 호출

```bash
for vu in alice bob carol; do
  curl -s -X POST "$AIPRO_URL/api/chat/sync" \
    -H "Content-Type: application/json" \
    -H "X-Virtual-User: $vu" \
    -d "{\"message\": \"안녕하세요\", \"thread_id\": \"$vu-session-001\"}"
  echo
done
```

### 2.4 대화 쓰레드 (`thread_id`)

- 서버는 `thread_id` 단위로 최근 대화 10개를 메모리에 보관하고, 다음 요청 시 LLM 에 함께 전달합니다.
- `thread_id` 를 생략하면 가상 사용자별 기본 쓰레드 `user_<virtual_user>` (예: `user_alice`) 를 사용합니다. 가상 사용자끼리 대화 이력이 섞이지 않습니다.
- 같은 가상 사용자가 여러 대화를 독립적으로 진행하려면 `thread_id` 를 직접 지정하세요. 권장 형식: `<virtual_user>-<세션ID>` (예: `alice-session-001`). 매 요청을 이력 없이 처리하려면 요청마다 새 `thread_id` 를 사용합니다.
- `X-Virtual-User` 헤더도 생략하면 `user_nobody` 쓰레드를 공유합니다.
- `thread_id` 는 가상 사용자와 무관한 단순 문자열 키입니다. 다른 가상 사용자가 같은 `thread_id` 를 지정하면 이력을 공유하게 되므로 겹치지 않게 지정하세요.
- 대화 이력은 서버 메모리에만 있으므로 서버 재시작 시 초기화됩니다.

### 2.5 응답의 `request_id` 로 로그 바로 찾기

```bash
RID=$(curl -s -X POST "$AIPRO_URL/api/chat/sync" \
  -H "Content-Type: application/json" \
  -H "X-Virtual-User: alice" \
  -d '{"message": "안녕하세요"}' | jq -r .request_id)

sleep 2   # Loki 적재 대기 (보통 1초 이내)

curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode "query={container=\"llm-agent\"} | request_id=\"$RID\"" \
  --data-urlencode 'since=10m' \
| jq -c '.data.result[].values[][1] | fromjson | {type, virtual_user, request_id, latency_ms}'
```

---

## 3. 로그 데이터 명세

Loki 에 저장되는 로그 한 줄(line)은 하나의 JSON 객체입니다. 앱의 로그 접두어(`[LLM_LOG]`, 시각, 레벨)는 Alloy 가 제거하므로 line 은 순수 JSON 입니다.

### 3.1 로그 타입 목록

같은 Loki 스트림(`{container="llm-agent"}`)에 아래 타입이 모두 쌓입니다. 채팅 통계만 필요하면 `request` / `response` / `error` 만 수집하세요 (§4.3).

| `type` | 발생 API | 설명 |
|---|---|---|
| `request` | `/api/chat/sync`, `/chat` | 채팅 요청 (LLM 호출 직전) |
| `response` | `/api/chat/sync`, `/chat` | 채팅 응답 (LLM 호출 완료) |
| `error` | `/api/chat/sync`, `/chat` | 채팅 LLM 호출 실패 |
| `rag_search_request` / `rag_search_response` / `rag_search_error` | `/api/rag/search` | RAG 검색 |
| `toollab_run_request` / `toollab_ai_step` / `toollab_tool_call` / `toollab_run_response` / `toollab_run_error` / `toollab_tool_parser_leak` / `toollab_tool_register` | Tool Lab | 도구 실행 / 등록 |

### 3.2 `type=request`

```json
{
  "request_id": "94073aa5",
  "user_id": "nobody",
  "virtual_user": "alice",
  "type": "request",
  "thread_id": "alice-session-001",
  "model_type": "chat",
  "messages": [
    {"role": "system", "content": "당신은 AI 어시스턴트입니다."},
    {"role": "human",  "content": "대한민국의 수도는?"}
  ],
  "timestamp": "2026-10-06T02:02:43.305243+00:00"
}
```

### 3.3 `type=response`

```json
{
  "request_id": "94073aa5",
  "thread_id": "alice-session-001",
  "user_id": "nobody",
  "virtual_user": "alice",
  "type": "response",
  "model_type": "chat",
  "model": "Qwen/Qwen3.8-27B",
  "input_tokens": 64,
  "output_tokens": 12,
  "latency_ms": 683,
  "full_response": "대한민국의 수도는 서울입니다.",
  "timestamp": "2026-10-06T02:02:43.988117+00:00"
}
```

### 3.4 `type=error`

```json
{
  "request_id": "94073aa5",
  "thread_id": "alice-session-001",
  "user_id": "nobody",
  "virtual_user": "alice",
  "type": "error",
  "error": "<오류 메시지>",
  "latency_ms": 30012,
  "timestamp": "2026-10-06T02:03:13.317000+00:00"
}
```

### 3.5 필드 명세 (채팅 로그)

| 필드 | 타입 | request | response | error | 설명 |
|---|---|:-:|:-:|:-:|---|
| `type` | string | O | O | O | §3.1 |
| `request_id` | string(8) | O | O | O | 요청 식별자. 채팅 API 응답의 `request_id` 와 동일 |
| `virtual_user` | string | O | O | O | 가상 사용자. **통계 집계 기준** |
| `user_id` | string | O | O | O | 인증 주체. 비 로그인 모드에서는 항상 `nobody` |
| `thread_id` | string | O | O | O | 대화 쓰레드 ID |
| `timestamp` | string | O | O | O | 로그 생성 시각. ISO 8601 UTC, 마이크로초 (`2026-10-06T02:02:43.305243+00:00`) |
| `model_type` | string | O | O | | `chat` / `reasoning` |
| `messages` | array | O | | | LLM 에 전달된 전체 메시지 `[{role, content}]`. role: `system` / `human` / `ai` |
| `model` | string \| null | | O | | 실제 응답한 모델명 |
| `input_tokens` / `output_tokens` | int \| null | | O | | 토큰 사용량. 모델에 따라 `null` 가능 |
| `latency_ms` | int | | O | O | 요청 로그 시점부터의 소요 시간 (ms) |
| `full_response` | string | | O | | LLM 응답 전문. 스트리밍 오류 시 빈 문자열 가능 |
| `error` | string | | | O | 오류 메시지 |

- 수집기는 **모르는 필드를 무시**하고 **필드 누락을 허용**하도록 구현하세요. 필드는 추가될 수 있습니다.
- `request` 로그의 `messages` 에는 쓰레드 이력(최대 10개)이 포함되므로 line 크기가 클 수 있습니다.

### 3.6 요청 단위 로그 묶음 규칙

하나의 요청은 같은 `request_id` 를 갖는 여러 로그로 기록됩니다.

| API | 성공 | LLM 실패 |
|---|---|---|
| `POST /api/chat/sync` | `request` → `response` | `request` → `error` |
| `POST /chat` (스트리밍) | `request` → `response` | `request` → `error` → `response` (`full_response` 빈 값, 토큰 `null`) |

수집기 구현 시 주의할 점:

- `request` 와 `response` 는 **서로 다른 polling 주기에 수집될 수 있습니다** (LLM 응답이 길거나 주기 경계에 걸친 경우). 묶음 처리는 수집 이후 단계에서 `request_id` 로 결합하세요.
- 서버 재시작 등으로 `response` / `error` 가 끝내 없는 `request` 가 있을 수 있습니다. 일정 시간(예: 10분) 후에도 짝이 없으면 미완료로 처리하세요.
- `request_id` 는 8자리 16진수라 **장기적으로 전역 유일하지 않습니다**. 레코드 식별은 §5.4 의 중복 제거 키를 사용하고, 요청 결합 시에도 `request_id` + `virtual_user` + 시간 근접성을 함께 보세요.

---

## 4. Loki 조회 API

### 4.1 사용할 API

| API | 용도 |
|---|---|
| `GET /loki/api/v1/query_range` | 기간 내 로그 원문 조회 (**polling 수집용**) |
| `GET /loki/api/v1/query` | 특정 시점 기준 집계 (통계용, §6) |
| `GET /ready` | Loki 상태 확인 (`ready` 응답) |

### 4.2 `query_range` 파라미터

| 파라미터 | 설명 |
|---|---|
| `query` | LogQL (§4.3) |
| `start` | 시작 시각 — **포함 (inclusive)**. Unix epoch 나노초 또는 RFC3339 |
| `end` | 종료 시각 — **미포함 (exclusive)**. 생략 시 현재 |
| `since` | `start` 대신 상대 기간 (예: `5m`, `1h`). 수집기에서는 `start`/`end` 를 명시하세요 |
| `limit` | 최대 건수. **상한 5000** — 초과 값을 주면 오류 |
| `direction` | `forward` (오래된 순) / `backward` (최신 순, **기본값**). 수집기는 반드시 `forward` |

### 4.3 LogQL 필터

인덱스 라벨(`type`, `model_type`)은 `{}` 안에서, 나머지 필드(`virtual_user`, `thread_id`, `request_id`, `model` 등)는 `|` 뒤에서 필터합니다. 별도 파싱(`| json`) 없이 바로 필터할 수 있습니다.

| 목적 | LogQL |
|---|---|
| 전체 LLM 로그 | `{container="llm-agent"}` |
| **채팅 로그만 (수집 권장)** | `{container="llm-agent", type=~"request\|response\|error"}` |
| 특정 가상 사용자 | `{container="llm-agent"} \| virtual_user="alice"` |
| 여러 가상 사용자 | `{container="llm-agent"} \| virtual_user=~"alice\|bob"` |
| 특정 쓰레드 | `{container="llm-agent"} \| thread_id="alice-session-001"` |
| 단일 요청 추적 | `{container="llm-agent"} \| request_id="94073aa5"` |
| 오류만 | `{container="llm-agent", type="error"}` |

> 수집용 쿼리에는 `| json` 을 붙이지 마세요. 필드가 stream 라벨로 중복 추출(`*_extracted`)되어 응답이 커지고, line 원문은 어차피 JSON 이므로 수집기에서 파싱하면 됩니다.

### 4.4 조회 예시

```bash
# 최근 10분, alice 의 로그 (오래된 순)
curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={container="llm-agent"} | virtual_user="alice"' \
  --data-urlencode 'since=10m' \
  --data-urlencode 'limit=100' \
  --data-urlencode 'direction=forward'
```

### 4.5 응답 형식

```json
{
  "status": "success",
  "data": {
    "resultType": "streams",
    "result": [
      {
        "stream": { "container": "llm-agent", "type": "response", "virtual_user": "alice", "request_id": "94073aa5", "...": "..." },
        "values": [
          [ "1791251278639254114", "{\"request_id\": \"94073aa5\", \"type\": \"response\", ...}" ]
        ]
      }
    ],
    "stats": { "...": "..." }
  }
}
```

| 항목 | 내용 |
|---|---|
| `result[]` | stream(라벨 조합) 단위 배열. 순서는 시간순이 아님 |
| `result[].stream` | 라벨 + structured metadata (`virtual_user`, `request_id` 등). 참고용이며, 데이터는 `values` 의 line 을 사용 |
| `result[].values[][0]` | Loki timestamp. **나노초, 19자리 문자열** |
| `result[].values[][1]` | 로그 line — §3 의 JSON 문자열 |

- 시간 순서가 필요하면 **모든 stream 의 `values` 를 합친 뒤 timestamp 로 정렬**하세요. `direction=forward` 는 각 stream 내부 순서와 `limit` 적용 기준만 보장합니다.
- 나노초 timestamp 는 64비트 정수로 다루거나 19자리 문자열로 비교하세요. 부동소수점(`double`, `jq` 의 `tonumber`)으로 변환하면 정밀도가 손실됩니다.

`jq` 로 한 줄에 하나씩 펼치기:

```bash
curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={container="llm-agent"}' \
  --data-urlencode 'since=10m' \
  --data-urlencode 'direction=forward' \
| jq -c '[.data.result[].values[]] | sort_by(.[0]) | .[] | {loki_ts: .[0]} + (.[1] | fromjson)'
```

### 4.6 오류 응답과 제한값

| 상황 | 응답 |
|---|---|
| 정상 | `200`, `"status": "success"` |
| `limit` > 5000 | `400`, 본문 `max entries limit per query exceeded, limit > max_entries_limit_per_query (6000 > 5000)` |
| LogQL 문법 오류 | `400`, 본문에 parse error 메시지 |
| Loki 기동 중 / 장애 | `503` 또는 연결 실패 — 재시도 대상 |

| Loki 설정 | 값 | 수집기 영향 |
|---|---|---|
| `max_entries_limit_per_query` | 5000 | 1회 조회 최대 건수 → 페이징 필요 |
| `max_query_length` | 30일 1시간 | 1회 조회 구간 상한 |
| `retention_period` | 30일 | 30일 지난 로그는 삭제 → 수집기 장기 중단 시 유실 |
| `max_line_size` | 256KB (잘라내지 않음) | 256KB 를 넘는 로그 line 은 Loki 에 적재되지 않음 |

---

## 5. 수집기 구현 가이드 (1분 주기 polling)

### 5.1 요구사항

| 항목 | 목표 |
|---|---|
| 주기 | 1분 |
| 누락 | Loki 에 적재된 로그는 빠짐없이 수집 |
| 중복 | 같은 로그를 두 번 저장하지 않음 (수집 단계 또는 저장소에서 제거) |
| 재시작 | 수집기 재시작 시 마지막 위치부터 이어서 수집 |
| 장애 | Loki 조회 실패 시 데이터 손실 없이 다음 주기에 재시도 |

### 5.2 반드시 알아야 할 Loki 동작 (실측)

| # | 동작 | 수집기 설계에 미치는 영향 |
|---|---|---|
| 1 | `start` 포함, `end` 미포함 | 구간 `[start, end)` 를 이어 붙이면 경계에서 누락·중복이 없음 |
| 2 | 정상 시 생성 → 조회 가능까지 약 1초 | `end` 를 현재 시각보다 조금 이전(LAG)으로 둬야 함 |
| 3 | **Alloy 중단 후 재기동 시, 중단 중 생성된 로그가 원래 timestamp 로 늦게 도착** (실측: 30초 중단 → 30초 전 timestamp 로 적재) | 커서보다 과거 timestamp 의 로그가 나중에 나타날 수 있음 → **겹침 재조회(OVERLAP) + 중복 제거 필수** |
| 4 | 1회 최대 5000건 | 페이징 필요 |
| 5 | 결과는 stream 별로 분할 | 합친 뒤 정렬 |

### 5.3 수집 알고리즘

**상태 (영속 저장)**

- `cursor` — 수집 완료 경계 (나노초). 이 시각 이전 구간은 수집 완료로 간주
- `seen` — 최근 OVERLAP 구간 안에서 이미 저장한 로그의 중복 제거 키 집합 (저장소의 unique 제약으로 대체 가능)

**권장 파라미터**

| 파라미터 | 권장값 | 설명 |
|---|---|---|
| `INTERVAL` | 60초 | polling 주기 |
| `LAG` | 10초 | `end = now - LAG`. 정상 적재 지연(약 1초) 흡수 |
| `OVERLAP` | 10분 | 매 주기 `cursor - OVERLAP` 부터 다시 조회. Alloy 일시 중단 등으로 늦게 도착한 로그 흡수 |
| `LIMIT` | 5000 | 1회 조회 건수 (최대값) |
| `MAX_WINDOW` | 1시간 | 밀린 구간(수집기 중단 후 재개 등)을 이 크기로 나눠 조회 |
| `INITIAL_START` | 운영 정책 | 커서가 없을 때 시작 시각. 최대 30일 이전까지 (보관 기간) |

**한 주기의 처리**

```
end   = now - LAG
start = cursor - OVERLAP                      # 겹침 재조회

for (wStart, wEnd) in split([start, end), MAX_WINDOW):   # 밀린 구간 분할
    s = wStart
    loop:
        resp = GET query_range(query, start=s, end=wEnd, limit=LIMIT, direction=forward)
        if 실패: 이번 주기 중단 (cursor 유지) → 다음 주기에 재시도
        entries = flatten(resp.data.result[].values) 를 timestamp 오름차순 정렬
        for e in entries:
            key = dedup_key(e)                # §5.4
            if key not in seen:
                저장(e); seen.add(key)
        if len(entries) < LIMIT: break        # 이 윈도우 완료
        s = entries[-1].timestamp             # 다음 페이지: 마지막 timestamp 부터 (포함, 중복은 seen 으로 제거)

cursor = end                                  # 모든 윈도우 성공 시에만 전진
seen 에서 (cursor - OVERLAP) 보다 오래된 키 정리
cursor, seen 영속 저장
```

구현 포인트:

- **커서 전진은 원자적으로**: 저장(로그 데이터)과 커서 갱신이 함께 성공해야 합니다. 중간 실패 시 커서를 전진시키지 않으면, 다음 주기에 같은 구간을 다시 읽고 중복 제거로 정리됩니다.
- **페이징 시작점은 마지막 timestamp 그대로 (+1 하지 않음)**: 같은 나노초에 여러 로그가 있을 수 있으므로, 포함 조회 후 중복 제거로 처리합니다. 한 페이지 5000건이 모두 같은 timestamp 인 경우는 사실상 없으나, 무한 반복 방지를 위해 페이지가 진전 없이 반복되면 `s + 1` 로 넘어가는 방어 로직을 두세요.
- **OVERLAP 을 넘는 지연은 수집되지 않습니다**: Alloy 가 OVERLAP(10분) 보다 오래 중단되면, 재기동 후 적재되는 과거 로그는 이미 지나간 구간에 들어갑니다. Alloy 상태를 모니터링하고(§7), 장시간 중단이 있었다면 커서를 중단 시점 이전으로 되돌려 재수집하세요 (중복은 자동 제거).
- **동시 실행 금지**: 이전 주기가 끝나기 전에 다음 주기가 시작되지 않도록 하세요 (단일 인스턴스, 또는 lock).

### 5.4 중복 제거 키

로그 line 의 필드로 키를 만듭니다.

```
dedup_key = request_id + "|" + type + "|" + timestamp
```

- `timestamp` 는 앱이 기록한 마이크로초 단위 시각이라, 같은 요청의 같은 타입이 여러 번 기록되는 경우(예: `toollab_ai_step`)에도 구분됩니다.
- 수집 결과를 DB 에 저장한다면 이 키에 **unique 제약**을 걸고 insert-or-ignore(upsert) 하는 방식이 가장 단순하고 안전합니다. 이 경우 `seen` 집합은 생략할 수 있습니다.
- Loki timestamp(`values[][0]`)도 함께 저장해 두면 재수집·검증 시 유용합니다.

### 5.5 curl 예시 — 한 주기 조회

커서가 `CURSOR` (나노초) 일 때 한 주기의 조회 요청입니다. 각 로그에 `loki_ts` 와 `dedup_key` 를 붙여 한 줄에 하나씩 출력합니다.

```bash
LAG=10; OVERLAP=600
END=$(( ($(date +%s) - LAG) * 1000000000 ))
START=$(( CURSOR - OVERLAP * 1000000000 ))

curl -sf -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={container="llm-agent", type=~"request|response|error"}' \
  --data-urlencode "start=$START" \
  --data-urlencode "end=$END" \
  --data-urlencode 'limit=5000' \
  --data-urlencode 'direction=forward' \
| jq -c '[.data.result[].values[]] | sort_by(.[0]) | .[]
         | (.[1] | fromjson) as $e
         | {loki_ts: .[0], dedup_key: "\($e.request_id)|\($e.type)|\($e.timestamp)"} + $e'
```

출력 예:

```json
{"loki_ts":"1791254558263115636","dedup_key":"a3eaaf37|request|2026-10-06T02:42:38.262004+00:00","request_id":"a3eaaf37","user_id":"nobody","virtual_user":"latetest","type":"request","thread_id":"late-1","model_type":"chat","messages":[...],"timestamp":"2026-10-06T02:42:38.262004+00:00"}
{"loki_ts":"1791254560076391477","dedup_key":"a3eaaf37|response|2026-10-06T02:42:40.075959+00:00","request_id":"a3eaaf37","thread_id":"late-1","user_id":"nobody","virtual_user":"latetest","type":"response","model_type":"chat","model":"Qwen/Qwen3.8-27B","input_tokens":64,"output_tokens":102,"latency_ms":1813,"full_response":"\n\nHi there! 👋 How's it going? ...","timestamp":"2026-10-06T02:42:40.075959+00:00"}
```

- 결과가 5000건이면 마지막 `loki_ts` 를 `start` 로 하여 같은 `END` 까지 다시 조회합니다 (§5.3).
- 모든 조회가 성공하면 `CURSOR=$END` 로 갱신합니다.

### 5.6 장애 시나리오별 동작

| 상황 | 결과 | 수집기 조치 |
|---|---|---|
| Loki 조회 실패 / 일시 장애 | 해당 주기 수집 안 됨 | 커서 유지, 다음 주기 재시도. 밀린 구간은 MAX_WINDOW 로 분할 조회 |
| 수집기 중단 후 재시작 | 중단 구간이 밀림 | 저장된 커서부터 재개. 중단이 30일을 넘으면 그 이전 로그는 Loki 에서 삭제되어 유실 |
| Alloy 중단 ≤ OVERLAP | 로그가 늦게 도착 | OVERLAP 재조회로 자동 수집 |
| Alloy 중단 > OVERLAP | 늦게 도착한 로그 일부 누락 | Alloy 중단 감지 시 커서를 중단 시점 이전으로 되돌려 재수집 |
| llm-agent 재시작 | 진행 중이던 요청은 `response` 없이 `request` 만 남을 수 있음 | §3.6 미완료 처리 |
| 같은 로그 재조회 | 중복 수신 | 중복 제거 키로 제거 |

### 5.7 구현 검증 체크리스트

| # | 시나리오 | 기대 결과 |
|---|---|---|
| 1 | 가상 사용자 여러 명으로 채팅 호출 후 2주기 수집 | `virtual_user` 별 `request`/`response` 가 모두 1건씩 수집 |
| 2 | 수집 직후 즉시 한 번 더 수집 | 새로 저장되는 건수 0 (중복 없음) |
| 3 | `LIMIT` 을 작게(예: 3) 설정하고 수집 | 페이징으로 전체 수집, Loki 집계 건수(§6)와 일치 |
| 4 | Loki 를 중지한 상태에서 수집 실행 | 실패 처리, 커서 유지. Loki 재기동 후 누락 없이 수집 |
| 5 | Alloy 를 1~2분 중지 → 채팅 호출 → Alloy 재기동 | 다음 주기들에서 중지 중 로그가 수집됨 (OVERLAP) |
| 6 | 수집기 재시작 | 커서부터 이어서 수집, 중복 없음 |
| 7 | 응답의 `request_id` 로 수집 데이터 조회 | `request` 와 `response` 가 같은 `request_id` 로 결합됨 |

수집 건수와 Loki 건수 대조용 쿼리:

```bash
# 최근 1시간 채팅 로그 총 건수 (Loki 기준)
curl -s -G "$LOKI_URL/loki/api/v1/query" --data-urlencode \
  'query=sum(count_over_time({container="llm-agent", type=~"request|response|error"} [1h]))' \
| jq -r '.data.result[0].value[1]'
```

---

## 6. 통계 조회 (Loki 집계)

수집 없이 Loki 에서 바로 집계할 수도 있습니다. `/loki/api/v1/query` 의 `[기간]` 이 집계 구간입니다. (보관 기간 30일 이내)

```bash
# 가상 사용자별 요청 수 (최근 24시간)
curl -s -G "$LOKI_URL/loki/api/v1/query" --data-urlencode \
  'query=sum by (virtual_user) (count_over_time({container="llm-agent", type="request"} [24h]))' \
| jq -r '.data.result[] | "\(.metric.virtual_user)\t\(.value[1])"'

# 가상 사용자별 출력 토큰 합계 (최근 24시간)
curl -s -G "$LOKI_URL/loki/api/v1/query" --data-urlencode \
  'query=sum by (virtual_user) (sum_over_time({container="llm-agent", type="response"} | json | unwrap output_tokens [24h]))' \
| jq -r '.data.result[] | "\(.metric.virtual_user)\t\(.value[1])"'

# 가상 사용자별 p95 응답 지연 ms (최근 1시간)
curl -s -G "$LOKI_URL/loki/api/v1/query" --data-urlencode \
  'query=quantile_over_time(0.95, {container="llm-agent", type="response"} | json | unwrap latency_ms [1h]) by (virtual_user)' \
| jq -r '.data.result[] | "\(.metric.virtual_user)\t\(.value[1])"'

# 가상 사용자별 오류 수 (최근 24시간)
curl -s -G "$LOKI_URL/loki/api/v1/query" --data-urlencode \
  'query=sum by (virtual_user) (count_over_time({container="llm-agent", type="error"} [24h]))'
```

시간대별 추이가 필요하면 `query_range` 에 `step` 을 지정합니다.

```bash
# 1시간 단위 가상 사용자별 요청 수 (최근 24시간)
curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query=sum by (virtual_user) (count_over_time({container="llm-agent", type="request"} [1h]))' \
  --data-urlencode 'since=24h' \
  --data-urlencode 'step=1h'
```

> `/chat` 스트리밍 오류 시 `response` 로그도 함께 남으므로(§3.6), 응답 기준 집계에서 오류 건을 제외하려면 `full_response` 가 빈 값인 `response` 를 거르거나 수집 데이터에서 `error` 와 결합해 판단하세요.

---

## 7. 운영 참고

| 항목 | 내용 |
|---|---|
| 보관 기간 | 30일. 이후 Loki 에서 자동 삭제 — 장기 보관은 수집기 저장소에서 |
| 수집 시작 시점 | Alloy 기동 이후 로그부터 적재. 그 이전 로그는 Loki 에 없음 |
| 보안 | Loki 는 인증이 없고, 로그에 프롬프트·응답 원문이 포함됨. 접근 가능한 네트워크를 제한할 것 |
| Loki 상태 | `curl -s $LOKI_URL/ready` → `ready` |
| Alloy 상태 | `curl -s http://<서버IP>:12345/-/ready` → `200`. 수집기에서 주기적으로 확인해 장시간 중단을 감지하면 §5.6 조치 |
| 로그가 조회되지 않을 때 | ① `docker ps` 로 `loki`, `alloy` 기동 확인 ② `docker logs llm-agent \| grep LLM_LOG` 로 앱 출력 확인 ③ `http://<서버IP>:12345` (Alloy UI) 에서 파이프라인 상태 확인 |
