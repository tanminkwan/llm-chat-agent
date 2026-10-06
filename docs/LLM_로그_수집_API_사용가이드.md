# LLM 로그 수집 API 사용 가이드

여러 가상 사용자(virtual user)로 aipro-plus 채팅 API 를 호출하고, Loki HTTP API 를 1분 주기로 polling 하여 LLM 로그를 수집하는 방법을 설명합니다. 모든 예시는 Linux `curl` 기준입니다.

---

## 1. 개요

```
 API 사용자                         aipro-plus 서버
┌──────────────┐  POST /api/chat/sync   ┌───────────┐ stdout  ┌───────┐ push ┌──────┐
│ 채팅 클라이언트 │ ─────────────────────▶ │ llm-agent │ ──────▶ │ Alloy │ ───▶ │ Loki │
│ (X-Virtual-User)│ ◀───── 응답 JSON ───── └───────────┘ [LLM_LOG]└───────┘      └──┬───┘
├──────────────┤                                                                │
│ 로그 수집기     │ ◀──────── GET /loki/api/v1/query_range (1분 주기 polling) ──────┘
└──────────────┘
```

- **채팅**: `POST /api/chat/sync` 요청 시 `X-Virtual-User` 헤더로 가상 사용자를 지정합니다. 응답에 로그 추적용 `request_id` 가 포함됩니다.
- **로그 기록**: 요청 1건당 `request` / `response` (실패 시 `error`) 로그가 남고, 모든 로그에 `virtual_user` 필드가 포함됩니다.
- **로그 수집**: Loki HTTP API 를 직접 호출합니다. 로그는 호출 후 약 1초 이내에 조회 가능합니다.

### 1.1 접속 정보

| 항목 | 주소 | 인증 |
|---|---|---|
| aipro-plus API | `http://<서버IP>:28000` | 없음 (비 로그인 모드) |
| Loki HTTP API | `http://<서버IP>:3100` | 없음 |

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

## 3. 로그 데이터 구조

로그 한 줄은 하나의 JSON 객체입니다. 요청 1건은 `request_id` 가 같은 `request` → `response` (또는 `error`) 쌍으로 기록됩니다.

### 3.1 `type=request`

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

### 3.2 `type=response`

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

### 3.3 `type=error`

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

### 3.4 필드 설명

| 필드 | 설명 |
|---|---|
| `virtual_user` | 가상 사용자. **통계 집계 기준** |
| `user_id` | 인증 주체. 비 로그인 모드에서는 항상 `nobody` |
| `request_id` | 요청 식별자 (8자). request/response/error 를 묶는 키이며 채팅 API 응답으로도 반환됨. 전역 유일하지 않으므로 장기 집계 시 `timestamp` 와 함께 사용 |
| `timestamp` | 로그 생성 시각 (ISO 8601, UTC) |
| `messages` | LLM 에 전달된 전체 메시지 (시스템 프롬프트 + 쓰레드 이력 + 이번 메시지) |
| `input_tokens` / `output_tokens` | 토큰 사용량 (모델에 따라 `null` 가능) |
| `latency_ms` | LLM 호출 소요 시간 (ms) |

> **참고**
> - 채팅 API 응답의 `request_id` (body 및 `X-Request-Id` 헤더) 로 해당 요청의 로그를 찾을 수 있습니다 (§2.5).
> - 같은 Loki 에 RAG 검색(`rag_search_*`), Tool Lab(`toollab_*`) 로그도 함께 쌓입니다. 채팅 로그만 필요하면 §4 의 `type` 필터를 사용하세요. (`/chat` 스트리밍 API 도 `request`/`response`/`error` 타입을 사용합니다.)

---

## 4. Loki 조회 API

### 4.1 사용할 API

| API | 용도 |
|---|---|
| `GET /loki/api/v1/query_range` | 기간 내 로그 원문 조회 (**polling 수집용**) |
| `GET /loki/api/v1/query` | 특정 시점 기준 집계 (통계용) |
| `GET /ready` | Loki 상태 확인 (`ready` 응답) |

### 4.2 `query_range` 파라미터

| 파라미터 | 설명 |
|---|---|
| `query` | LogQL (아래 §4.3) |
| `start` | 시작 시각 — **포함**. Unix epoch 나노초 또는 RFC3339 |
| `end` | 종료 시각 — **미포함**. 생략 시 현재 |
| `since` | `start` 대신 상대 기간 (예: `5m`, `1h`) |
| `limit` | 최대 건수. **상한 5000** (초과 시 오류) |
| `direction` | `forward` (오래된 순) / `backward` (최신 순, 기본값) |

### 4.3 LogQL 필터

인덱스 라벨(`type`, `model_type`)은 `{}` 안에서, 나머지 필드(`virtual_user`, `thread_id`, `request_id`, `model` 등)는 `|` 뒤에서 필터합니다. 별도 파싱(`| json`) 없이 바로 필터할 수 있습니다.

| 목적 | LogQL |
|---|---|
| 전체 LLM 로그 | `{container="llm-agent"}` |
| 채팅 로그만 | `{container="llm-agent", type=~"request\|response\|error"}` |
| 특정 가상 사용자 | `{container="llm-agent"} \| virtual_user="alice"` |
| 여러 가상 사용자 | `{container="llm-agent"} \| virtual_user=~"alice\|bob"` |
| 특정 쓰레드 | `{container="llm-agent"} \| thread_id="alice-session-001"` |
| 단일 요청 추적 | `{container="llm-agent"} \| request_id="94073aa5"` |
| 오류만 | `{container="llm-agent", type="error"}` |

### 4.4 조회 예시

```bash
# 최근 10분, alice 의 로그 (오래된 순)
curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={container="llm-agent"} | virtual_user="alice"' \
  --data-urlencode 'since=10m' \
  --data-urlencode 'limit=100' \
  --data-urlencode 'direction=forward'
```

### 4.5 응답 형식과 파싱

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
    ]
  }
}
```

- 결과는 여러 `stream` 으로 나뉘어 옵니다 (라벨 조합별). **모든 stream 의 `values` 를 합친 뒤 timestamp 로 정렬**해야 시간 순서가 됩니다.
- `values[i][0]`: Loki timestamp (나노초, 19자리 문자열)
- `values[i][1]`: 로그 원문 — §3 의 JSON 문자열

`jq` 로 한 줄에 하나씩 JSON 으로 펼치기:

```bash
curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={container="llm-agent"}' \
  --data-urlencode 'since=10m' \
  --data-urlencode 'direction=forward' \
| jq -c '[.data.result[].values[]] | sort_by(.[0]) | .[] | {loki_ts: .[0]} + (.[1] | fromjson)'
```

> 나노초 timestamp 는 `jq` 의 `tonumber` 로 변환하면 정밀도가 손실됩니다. 정렬·비교는 문자열(19자리 고정)로 하세요.

---

## 5. 1분 주기 polling 수집

### 5.1 수집 원칙

1. **구간 커서**: 직전 수집의 `end` 를 다음 수집의 `start` 로 사용합니다. Loki 는 `start` 포함 / `end` 미포함이므로 구간 `[start, end)` 를 이어 붙이면 **누락·중복이 없습니다**.
2. **지연 여유(LAG)**: `end = 현재시각 - 10초` 로 둡니다. 로그는 생성 후 Loki 에 도착하기까지 지연(측정치 약 1초)이 있으므로, 아직 도착하지 않은 로그를 건너뛰지 않도록 최근 10초는 다음 주기에 가져갑니다.
3. **페이징**: 한 구간의 결과가 `limit`(5000) 과 같으면 마지막 timestamp + 1ns 부터 같은 `end` 까지 다시 조회합니다.
4. **실패 시 커서 유지**: 조회가 실패하면 커서를 전진시키지 않고 다음 주기에 같은 구간을 다시 조회합니다.
5. **커서 영속화**: 커서를 파일에 저장해 수집기 재시작 후에도 이어서 수집합니다.

### 5.2 수집 스크립트

저장소의 `scripts/llm_log_poller.sh` 를 사용합니다. (의존: `curl`, `jq`)

```bash
#!/usr/bin/env bash
# llm_log_poller.sh — Loki 에서 [LLM_LOG] 를 주기적으로 수집해 JSON Lines 파일로 저장한다.
# 의존: curl, jq
set -uo pipefail

LOKI_URL="${LOKI_URL:-http://localhost:3100}"
QUERY="${QUERY:-{container=\"llm-agent\"}}"   # 수집 대상 LogQL
INTERVAL="${INTERVAL:-60}"                    # 폴링 주기 (초)
LAG="${LAG:-10}"                              # 수집 지연 여유 (초). now-LAG 까지만 조회
LIMIT="${LIMIT:-5000}"                        # 1회 조회 최대 건수 (Loki 상한 5000)
INITIAL_LOOKBACK="${INITIAL_LOOKBACK:-300}"   # 최초 실행 시 과거 몇 초부터 가져올지
CURSOR_FILE="${CURSOR_FILE:-./llm_log.cursor}"
OUT_FILE="${OUT_FILE:-./llm_log.jsonl}"
ONCE="${ONCE:-0}"                             # 1 이면 한 번만 수집하고 종료 (cron 용)

now_ns() { echo $(( $(date +%s) * 1000000000 )); }

[ -f "$CURSOR_FILE" ] || echo $(( $(now_ns) - INITIAL_LOOKBACK * 1000000000 )) > "$CURSOR_FILE"

poll_once() {
  local start end cur resp page n
  start=$(cat "$CURSOR_FILE")
  end=$(( $(now_ns) - LAG * 1000000000 ))
  [ "$start" -lt "$end" ] || return 0

  cur=$start
  while :; do
    # 조회 구간은 [cur, end) — start 포함, end 미포함
    if ! resp=$(curl -sf -G "$LOKI_URL/loki/api/v1/query_range" \
          --data-urlencode "query=$QUERY" \
          --data-urlencode "start=$cur" \
          --data-urlencode "end=$end" \
          --data-urlencode "limit=$LIMIT" \
          --data-urlencode "direction=forward"); then
      echo "$(date -Is) WARN loki query failed; cursor 유지, 다음 주기에 재시도" >&2
      return 1
    fi

    # 여러 stream 을 하나로 펼쳐 timestamp(ns, 19자리 문자열) 순으로 정렬
    page=$(jq -c '[.data.result[].values[]] | sort_by(.[0]) | .[]
                  | {loki_ts: .[0]} + (.[1] | fromjson)' <<<"$resp")
    n=$(grep -c . <<<"$page" || true)

    if [ "$n" -gt 0 ]; then
      printf '%s\n' "$page" >> "$OUT_FILE"
    fi
    echo "$(date -Is) INFO fetched=$n window=[$cur,$end)" >&2

    # limit 만큼 꽉 찼으면 같은 구간 안에서 다음 페이지 조회
    if [ "$n" -ge "$LIMIT" ]; then
      cur=$(( $(tail -n1 <<<"$page" | jq -r .loki_ts) + 1 ))
    else
      break
    fi
  done

  echo "$end" > "$CURSOR_FILE"   # 구간 전체 성공 시에만 커서 전진
}

while :; do
  poll_once
  [ "$ONCE" = "1" ] && break
  sleep "$INTERVAL"
done
```

### 5.3 실행

**상주 실행 (1분 주기)**

```bash
export LOKI_URL=http://10.10.20.11:3100
nohup ./scripts/llm_log_poller.sh >> llm_log_poller.log 2>&1 &
```

**cron 실행 (1분 주기, 1회 수집 후 종료)**

```cron
* * * * * cd /opt/llm-log && LOKI_URL=http://10.10.20.11:3100 ONCE=1 /opt/llm-log/llm_log_poller.sh >> poller.log 2>&1
```

> cron 사용 시 이전 실행이 끝나기 전에 다음 실행이 겹치지 않도록 `flock -n /tmp/llm_log_poller.lock <명령>` 으로 감싸는 것을 권장합니다.

**설정 변수**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LOKI_URL` | `http://localhost:3100` | Loki 주소 |
| `QUERY` | `{container="llm-agent"}` | 수집 대상 LogQL (§4.3). 예: `{container="llm-agent"} \| virtual_user=~"alice\|bob"` |
| `INTERVAL` | `60` | polling 주기 (초) |
| `LAG` | `10` | 수집 지연 여유 (초) |
| `LIMIT` | `5000` | 1회 조회 건수 (최대 5000) |
| `INITIAL_LOOKBACK` | `300` | 커서 파일이 없을 때 과거 몇 초부터 수집할지 |
| `CURSOR_FILE` | `./llm_log.cursor` | 커서 저장 파일 |
| `OUT_FILE` | `./llm_log.jsonl` | 수집 결과 (JSON Lines, append) |
| `ONCE` | `0` | `1` 이면 1회 수집 후 종료 |

**출력 예 (`llm_log.jsonl`)** — Loki timestamp(`loki_ts`) 가 추가된 §3 형식의 JSON 이 한 줄에 하나씩 쌓입니다.

```json
{"loki_ts":"1791252163305607742","request_id":"616a10b4","thread_id":"dave-s1","user_id":"nobody","virtual_user":"dave","type":"response","model_type":"chat","model":"Qwen/Qwen3.8-27B","input_tokens":65,"output_tokens":39,"latency_ms":683,"full_response":"안녕하세요! 무엇을 도와드릴까요?","timestamp":"2026-10-06T02:02:43.305243+00:00"}
```

수집 파일에서 바로 가상 사용자별 건수 확인:

```bash
jq -r 'select(.type=="request") | .virtual_user' llm_log.jsonl | sort | uniq -c
```

### 5.4 과거 데이터 일괄 수집 (backfill)

커서 파일을 원하는 시작 시각(나노초)으로 만든 뒤 `ONCE=1` 로 실행하면 해당 시각부터 현재까지 페이징하며 수집합니다.

```bash
echo $(( $(date -d '2026-10-06 00:00:00' +%s) * 1000000000 )) > llm_log.cursor
ONCE=1 ./scripts/llm_log_poller.sh
```

---

## 6. 통계 조회 (Loki 집계)

수집 없이 Loki 에서 바로 집계할 수도 있습니다. `/loki/api/v1/query` 의 `[기간]` 이 집계 구간입니다.

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

---

## 7. 운영 참고

| 항목 | 내용 |
|---|---|
| 보관 기간 | 720시간 (30일). 이후 Loki 에서 자동 삭제 — 장기 보관은 polling 수집 파일로 |
| 수집 시작 시점 | Alloy 기동 이후 로그부터 적재. 그 이전 로그는 Loki 에 없음 |
| 보안 | Loki 는 인증이 없고, 로그에 프롬프트·응답 원문이 포함됨. 접근 가능한 네트워크를 제한할 것 |
| 상태 확인 | `curl -s $LOKI_URL/ready` → `ready` |
| 로그가 조회되지 않을 때 | ① `docker ps` 로 `loki`, `alloy` 기동 확인 ② `docker logs llm-agent \| grep LLM_LOG` 로 앱 출력 확인 ③ `http://<서버IP>:12345` (Alloy UI) 에서 파이프라인 상태 확인 |
