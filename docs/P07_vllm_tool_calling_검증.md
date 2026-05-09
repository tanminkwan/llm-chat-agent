# Phase 07 — Production vLLM Tool Calling 검증 시나리오

> 본 문서는 Phase 07 설계 착수 **전에** 운영 vLLM 인스턴스 2종(gpt-oss, Qwen3-Coder)이
> 요구사항 §4.3 의 가정대로 동작하는지 직접 검증하기 위한 테스트 케이스 모음이다.
> 각 케이스는 **무엇을 입증하는가 / 명령 / 예상 결과 / 실제 결과 (테스터 기록란)** 로 구성된다.

## 0. 사전 준비

### 0.1 환경 변수 (테스트 시작 전 1회 export)

```bash
# Qwen3-Coder vLLM
export QWEN_URL="http://<qwen-host>:<port>/v1"
export QWEN_KEY="<token-or-dummy>"
export QWEN_MODEL="$(curl -s $QWEN_URL/models -H "Authorization: Bearer $QWEN_KEY" | jq -r '.data[0].id')"

# gpt-oss vLLM
export GPTOSS_URL="http://<gpt-oss-host>:<port>/v1"
export GPTOSS_KEY="<token-or-dummy>"
export GPTOSS_MODEL="$(curl -s $GPTOSS_URL/models -H "Authorization: Bearer $GPTOSS_KEY" | jq -r '.data[0].id')"

echo "QWEN  = $QWEN_MODEL"
echo "GPTOSS= $GPTOSS_MODEL"
```

### 0.2 공용 도구 정의 (재사용)
아래 셸 함수를 한 번 정의해 두면 각 테스트에서 모델·도구만 바꿔 호출할 수 있다.

```bash
chat () {
  # $1 = URL, $2 = KEY, $3 = MODEL, $4 = JSON payload (without model field)
  curl -s "$1/chat/completions" \
    -H "Authorization: Bearer $2" -H "Content-Type: application/json" \
    -d "$(jq -nc --arg m "$3" --argjson p "$4" '$p + {model:$m}')" \
  | jq '.choices[0].message | {content, reasoning_content, tool_calls}'
}
```

> 셸 의존성: `curl`, `jq`. 둘 다 표준 패키지.

### 0.3 결과 기록 규칙
- ✅ PASS / ❌ FAIL / ⚠️ PARTIAL 중 하나로 판정.
- "실제 결과" 란에는 `tool_calls` 배열의 길이, 첫 호출의 `name`, `arguments` 그리고 `content` 의 앞 100자 정도를 적는다.
- 차이가 있는 경우 가설 (§1) 과 어떻게 연결되는지 **한 줄 코멘트**.

### 0.4 `tool_call_id` 와 vLLM 의 stateless 모델 (사전 못박기)

본 문서 곳곳에 `tool_call_id` 가 등장하므로 한 번에 정리해 둔다 — 이걸 못박지 않으면
T5 류 케이스 결과 해석에서 반복적으로 혼동이 생긴다.

- **vLLM 은 완전 stateless**. 매 `/v1/chat/completions` 호출은 독립이며, **이전 turn 에서
  서버가 발급한 `tool_call_id` 를 서버가 기억하지 않는다.** 클라이언트가 매번 전체 history
  (assistant.tool_calls 포함) 를 통째로 실어 보내고, vLLM 은 그 페이로드만 보고 응답한다.
- **id 의 진짜 고객은 클라이언트**. 병렬 도구 호출 결과가 비동기로 뒤섞여 도착할 때, 어느
  결과가 어느 호출에 대응하는지 매칭하기 위한 **client-side correlation token**.
- **wire-level 정합성**: 같은 페이로드 안에서 `assistant.tool_calls[i].id` 와
  `tool.tool_call_id` 만 일치하면 vLLM 은 만족. 불일치해도 vLLM 은 (현재 가정상) 거절하지
  않는다 — T5/T5d 에서 직접 검증.
- **이전 completion 의 id 를 echo back 해야 하는가?** vLLM 만 상대한다면 **불필요**. 클라가
  임의로 만든 id 를 써도 같은 페이로드 안에서 일관되기만 하면 동작한다. 그럼에도 echo back 을
  하는 이유는 모두 **관행**: ① OpenAI 본가 strict 모드 호환, ② SDK 자동 처리(LangChain 의
  `AIMessage.tool_calls[i].id` → `ToolMessage(tool_call_id=...)`), ③ 로그/트레이스 정합성,
  ④ 감사/재현성. **vLLM 자체의 요구는 아님.**
- **모델이 id 를 routing 단서로 쓰는가**: chat template 이 id 를 prompt 에 박는지에
  좌우. 대부분 안 박음 → id 는 cosmetic. T5d 에서 swap 으로 직접 입증.

---

## T1. 헬스체크 + 모델 ID 확인

### 입증 대상
- vLLM 인스턴스가 살아 있고 OpenAI-compatible `/v1/models` 응답이 정상.
- 운영자가 알고 있는 모델명과 실제 서빙 중인 모델 ID 가 일치.

### 명령
```bash
curl -s $QWEN_URL/models   -H "Authorization: Bearer $QWEN_KEY"   | jq '.data[].id'
curl -s $GPTOSS_URL/models -H "Authorization: Bearer $GPTOSS_KEY" | jq '.data[].id'
```

### 예상 결과
- Qwen 측: `"Qwen/Qwen3-Coder-..."` 또는 `Qwen3-Coder-30B-A3B` / `480B-A35B` 류 1건.
- gpt-oss 측: `"gpt-oss-20b"` 또는 `"gpt-oss-120b"` 류 1건.
- 둘 다 비어있지 않은 배열.

### 실제 결과
- Qwen: ` `
- gpt-oss: ` `
- 판정: ` `

---

## T2. 단일 도구 호출 (파서 동작 검증) — **가장 중요**

### 입증 대상
- vLLM 양쪽 모두 `--enable-auto-tool-choice --tool-call-parser <X>` 가 실제로 켜져 있어,
  모델이 발생시킨 tool-call 마크업을 **`tool_calls` 구조화 필드로 변환** 하는지.
- 만약 `content` 에 `<tool_call>...` 류 raw 마크업이 새어 나오면 §4.3.3 누수 — Phase 07 의 실행 루프가
  첫 턴에 빠져나오는 원인이 된다.

### 페이로드
```bash
PAYLOAD_T2=$(jq -nc '{
  messages: [{role:"user", content:"Add 7 and 13 using the tool."}],
  tools: [{
    type:"function",
    function:{
      name:"add",
      description:"Add two integers and return the sum.",
      parameters:{
        type:"object",
        properties:{a:{type:"integer"}, b:{type:"integer"}},
        required:["a","b"]
      }
    }
  }],
  tool_choice:"auto"
}')
```

### 명령
```bash
echo "=== Qwen3-Coder ==="
chat "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "$PAYLOAD_T2"

echo "=== gpt-oss ==="
chat "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "$PAYLOAD_T2"
```

### 예상 결과
양쪽 모두:
- `tool_calls` 배열 길이 ≥ 1
- `tool_calls[0].function.name == "add"`
- `tool_calls[0].function.arguments` JSON 파싱 시 `{a:7, b:13}` (또는 의미상 동일)
- `content` 는 `null` 또는 빈 문자열

❌ Fail 시그널:
- `tool_calls` 가 `null` / `[]` 인데 `content` 에 `<tool_call>`, `<|tool▁call▁begin|>`, ` ```json\n{"name":` 같은 마크업이 보임 → **파서 미설정 또는 파서 종류 mismatch**

### 실제 결과
- Qwen3-Coder:
  - tool_calls 길이: 1
  - 첫 호출 name/args: `add({a:7, b:13})` (예상 그대로)
  - content: 없음 / null
  - `reasoning_content`: **없음** (필드 부재 또는 null) — §6.4 가정 일치
  - 판정: ✅ **PASS** — `qwen3_coder` 파서 정상 동작
- gpt-oss:
  - tool_calls 길이: 1
  - 첫 호출 name/args: `add({a:7, b:13})` (예상 그대로)
  - content: 없음 / null
  - `reasoning_content`: **있음** — `"We need to call the add tool with a=7, b=13."`
  - 판정: ✅ **PASS** — `openai` 파서 정상 동작 + reasoning 채널까지 확인

> **부수 입증**: T7 의 gpt-oss reasoning_content 별도 필드 가정이 단순 prompt 에서도 성립.
> Phase 07 설계 시 `AIMessage.additional_kwargs["reasoning_content"]` 캡처는 **선택이 아니라 필수** —
> 안 하면 gpt-oss 트레이스에서 추론 텍스트가 통째로 누락된다.

---

## T3. 도구 불필요 케이스 (`tool_choice="auto"` 자율 판단)

### 입증 대상
- 도구가 노출돼 있어도 **굳이 안 써도 되는 입력** 에서는 모델이 자연어로 응답하는지.
- vLLM 의 `tool_choice="auto"` 가 OpenAI 스펙대로 동작하는지.

### 페이로드
```bash
PAYLOAD_T3=$(jq -nc '{
  messages: [{role:"user", content:"Hello! What can you do?"}],
  tools: [{
    type:"function",
    function:{
      name:"add",
      description:"Add two integers.",
      parameters:{type:"object", properties:{a:{type:"integer"},b:{type:"integer"}}, required:["a","b"]}
    }
  }],
  tool_choice:"auto"
}')
```

### 명령
```bash
chat "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "$PAYLOAD_T3"
chat "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "$PAYLOAD_T3"
```

### 예상 결과
양쪽 모두:
- `tool_calls` 가 `null` / `[]`
- `content` 에 자연어 인사/소개 응답

⚠️ 일부 코딩 특화 모델은 도구가 보이면 무조건 호출하려는 편향이 있음 — 그 경우 `tool_calls` 에 `add` 호출이 나올 수 있는데, **이는 Phase 07 비교 가설에서 "도구 호출 빈도" 라는 정량 지표로 잡힌다**(현상 자체가 데이터).

### 실제 결과
- Qwen3-Coder:
  - tool_calls 길이: 0 (null/[])
  - content 발췌: 자연어 인사/소개 응답
  - 판정: ✅ **PASS** — `tool_choice="auto"` 자율 판단 정상
- gpt-oss:
  - tool_calls 길이: 0 (null/[])
  - content 발췌: 자연어 인사/소개 응답
  - 판정: ✅ **PASS** — `tool_choice="auto"` 자율 판단 정상

> **부수 확인**: 두 모델 모두 "도구 보이면 무조건 호출" 편향 없음. Phase 07 §10.2 의 `model_type` 토글 라벨링은 그대로 둬도 무방.

---

## T4. 병렬 도구 호출 (parallel_tool_calls)

### 입증 대상
- 한 턴에 여러 개의 tool_call 을 **동시에** 발행할 수 있는지 — Phase 07 §6.2 실행 루프의 inner for-loop 가
  실제로 의미를 갖는지(아니면 사실상 매 턴 1회로 굳어지는지).
- 모델별 차이 확인: gpt-oss 는 일반적으로 ✓, Qwen3-Coder 는 변종/버전에 따라 다름.

### 페이로드
```bash
PAYLOAD_T4=$(jq -nc '{
  messages: [{role:"user", content:"Get the current weather in Seoul, Tokyo, and New York."}],
  tools: [{
    type:"function",
    function:{
      name:"get_weather",
      description:"Get current weather for a city.",
      parameters:{type:"object", properties:{city:{type:"string"}}, required:["city"]}
    }
  }],
  tool_choice:"auto",
  parallel_tool_calls: true
}')
```

### 명령
```bash
chat "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "$PAYLOAD_T4"
chat "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "$PAYLOAD_T4"
```

### 예상 결과
- gpt-oss: `tool_calls` 길이 = **3** (Seoul / Tokyo / New York 각 1건). 한 턴에 다 발행.
- Qwen3-Coder: 길이 = **3 또는 1**.
  - 3 이면 parallel 지원 OK.
  - 1 이면 sequential 모델 — Phase 07 트레이스에서 같은 prompt 가 iterations 더 많게 찍힐 것을 예고.
- 둘 다 첫 호출 name = `get_weather`.

### 실제 결과
- Qwen3-Coder:
  - tool_calls 길이: **3** (한 턴에 모두)
  - 호출들의 city 인자 모음: Seoul / Tokyo / New York
  - 판정: ✅ **PASS (parallel)**
- gpt-oss:
  - tool_calls 길이: **1** (Seoul 만)
  - 호출들의 city 인자 모음: Seoul
  - `reasoning_content`: "각 도시(Seoul, Tokyo, New York) 에 대해 get_weather 를 호출해야 한다" 취지의 명시적 계획 포함
  - 판정: ⚠️ **PARTIAL — parallel 미발생, sequential 의도 + reasoning 으로 계획만 노출** (실제 sequential iteration 으로 이어지는지는 T4b 에서 실측)

> **중요한 거동 차이 (§1 가설 보강 필요)**:
> gpt-oss 는 `parallel_tool_calls: true` 가 켜져 있어도 **순차 호출을 선호**한다 (harmony `analysis` 채널에서 plan 하고 `commentary` 에서 첫 1개만 emit). 이는 vLLM 파서 문제가 아니라 **모델 학습 특성**.
>
> 그 결과 같은 prompt 에 대해:
> - Qwen3-Coder: iterations=1, tool_calls 합=3 (빠른 실행)
> - gpt-oss: iterations≈3, tool_calls 합=3 (계획 노출 + 단계별 실행)
>
> **§1 의 단순 가설 ("reasoning 강함 = iterations 적음") 은 성립하지 않는다.** iterations 와 reasoning 노출은 **직교 축**. Phase 07 비교 패널은 두 축을 분리해 표시해야 하며, "iterations 적은 쪽 = 우수" 식 해석을 절대 권장 X.

---

## T4b. 병렬/순차 iteration 실측 (T4 의 multi-turn 확장)

### 입증 대상
- T4 의 ⚠️ PARTIAL 은 **turn 1 만 관찰** 한 결과. gpt-oss `reasoning_content` 의 plan
  ("Seoul/Tokyo/NY 각각 호출하겠다") 이 **실제 turn 2 이후 도구 호출로 이어지는지** 는 검증
  안 됨. T4b 는 fake tool result 를 턴마다 주입해 **iterations 를 실측** 한다.
- §6.0 표의 parallel(Qwen=iterations 1) / sequential(gpt-oss=iterations 3) 행을 실측 데이터로
  validation. 현재는 가정 상태.
- Phase 07 §6.2 루프의 max_iterations 가드 / 종료 조건 / sequential 모델 처리 설계의 직접 입력.

### 시나리오
- prompt: T4 와 동일 (`"Get the current weather in Seoul, Tokyo, and New York."`)
- 도구: T4 와 동일 (`get_weather(city)`).
- 매 turn 의 모델 응답에서 `tool_calls` 가 비어있을 때까지:
  1. `tool_calls` 추출.
  2. 각 호출에 대해 fake 결과 (`{"city":"<X>","temp_c":20}`) 주입.
  3. 다음 turn 페이로드 송신.
- 종료 조건: `tool_calls=null/[]` 또는 안전 한계 (max=6 turn) 도달.

### 양 모델의 기대 경로 (§6.0 표 실측 검증)
- **Qwen3-Coder** (T4 ✅ parallel):
  - turn 1: 3 호출 발행 → fake 결과 3개 주입
  - turn 2: `tool_calls=null`, 자연어 요약
  - 실측 → **iterations=2, tool_calls 총수=3**
- **gpt-oss** (T4 ⚠️ sequential plan):
  - turn 1: Seoul 호출 → fake 결과 주입
  - turn 2: Tokyo 호출 → fake 결과 주입
  - turn 3: NY 호출 → fake 결과 주입
  - turn 4: `tool_calls=null`, 자연어 요약
  - 실측 → **iterations=4, tool_calls 총수=3**

### gpt-oss turn 2 가 핵심 분기

| gpt-oss turn 2 응답 | 해석 | Phase 07 시사점 |
| :--- | :--- | :--- |
| `tool_calls=[Tokyo]` | ✅ sequential iteration 가설 입증. §6.0 sequential 행 확정 | §6.2 루프 의미 있음. iterations 카운터 + max_iterations 가드 필수 |
| `tool_calls=null`, Seoul 만 요약 | ❌ 모델이 turn 1 결과만으로 종료 → §1 가설 실패 | §6.2 루프가 gpt-oss 에서 무의미 — prompt/system 에 "모든 도시 답해라" 강제 필요 |
| `tool_calls=[Tokyo, NY]` | ⚠️ 지연된 parallel — turn 1 만 sequential, 이후 묶음 | 트레이스 라벨에 "first-turn-only sequential" 별도 기록 |
| 같은 도구 반복 / 무한 루프 | ❌ §6.2 max_iterations 가드 즉시 필요 신호 | 안전 한계 우선 구현 |

### 페이로드 빌더 (loop 형태)

```bash
TOOLS_T4B=$(jq -nc '[{
  type:"function",
  function:{name:"get_weather",
    description:"Get current weather for a city.",
    parameters:{type:"object",
      properties:{city:{type:"string"}},
      required:["city"]}}
}]')

INIT_T4B=$(jq -nc --argjson tools "$TOOLS_T4B" '{
  messages:[{role:"user",
    content:"Get the current weather in Seoul, Tokyo, and New York."}],
  tools:$tools, tool_choice:"auto", parallel_tool_calls:true
}')

run_t4b () {
  # $1=URL $2=KEY $3=MODEL $4=label
  local URL="$1" KEY="$2" MODEL="$3" LABEL="$4"
  local payload="$INIT_T4B" turn=1 max=6
  while [ $turn -le $max ]; do
    local resp
    resp=$(curl -s "$URL/chat/completions" \
      -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
      -d "$(jq -nc --arg m "$MODEL" --argjson p "$payload" '$p + {model:$m}')")
    echo "=== $LABEL turn $turn ==="
    echo "$resp" | jq '.choices[0].message
                       | {content,
                          tool_calls_len:(.tool_calls // [] | length),
                          cities:(.tool_calls // []
                                  | map(.function.arguments | fromjson | .city))}'

    local tc_len
    tc_len=$(echo "$resp" | jq '.choices[0].message.tool_calls // [] | length')
    [ "$tc_len" = "0" ] && break

    local assistant tool_msgs
    assistant=$(echo "$resp" | jq -c '.choices[0].message')
    tool_msgs=$(echo "$resp" | jq -c '
      .choices[0].message.tool_calls
      | map({role:"tool",
             tool_call_id:.id,
             content:((.function.arguments|fromjson|.city) as $c
                      | "{\"city\":\"\($c)\",\"temp_c\":20}")})')
    payload=$(jq -nc \
      --argjson p "$payload" \
      --argjson assistant "$assistant" \
      --argjson tool_msgs "$tool_msgs" '
      $p | .messages += [$assistant] + $tool_msgs')
    turn=$((turn+1))
  done
  echo "=== $LABEL terminated at turn $turn (iterations = $turn) ==="
}

run_t4b "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "Qwen3-Coder"
run_t4b "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "gpt-oss"
```

> 안전 한계 max=6 으로 잡은 이유: 기대 경로 (Qwen=2, gpt-oss=4) 의 1.5배 + 무한 루프 조기
> 차단. 무한 루프 발생하면 그 자체가 §6.2 가드 시급성의 데이터.

### 실제 결과
- Qwen3-Coder:
  - 도달 iterations: ` `
  - turn 별 tool_calls 길이 / 호출 도시: ` `
  - 최종 자연어 요약 발췌 (≤100자): ` `
  - 판정: ` `
- gpt-oss:
  - 도달 iterations: ` `
  - turn 별 tool_calls 길이 / 호출 도시: ` `
  - 최종 자연어 요약 발췌 (≤100자): ` `
  - turn 2 분기 (위 표 어느 행): ` `
  - 판정: ` `

> **§6.0 의 parallel/sequential 행이 실측으로 확정되는 케이스**. T4b 결과가 §6.0 표의
> "iterations" 칸을 가설에서 사실로 격상시키며, T6 onboarding 시나리오 iterations 만
> 본 구현 트레이스로 위임됨.

---

## T5. Multi-turn 체인 (실행 루프 시뮬레이션)

### 입증 대상
- Phase 07 §6.2 의 실행 루프 (assistant → tool result → assistant ...) 가 실제로 chain 되는지.
- 두 번째 턴에서 모델이 직전 `tool` 메시지를 **읽고** 후속 호출을 결정하는지.

### 시나리오
- prompt: "List all current users, then create a new user named Alice with email alice@example.com"
- 도구: `list_users()` , `create_user(name, email)`
- 기대 흐름: turn1 → `list_users` 호출 → 클라이언트가 가짜 결과 주입 → turn2 → `create_user` 호출.

### Turn 1 페이로드
```bash
TOOLS_T5='[
  {"type":"function","function":{
    "name":"list_users",
    "description":"List all current users.",
    "parameters":{"type":"object","properties":{}}
  }},
  {"type":"function","function":{
    "name":"create_user",
    "description":"Create a new user.",
    "parameters":{"type":"object",
      "properties":{"name":{"type":"string"},"email":{"type":"string"}},
      "required":["name","email"]}
  }}
]'

PAYLOAD_T5_TURN1=$(jq -nc --argjson tools "$TOOLS_T5" '{
  messages: [{role:"user",
              content:"List all current users, then create a new user named Alice with email alice@example.com."}],
  tools: $tools,
  tool_choice:"auto"
}')
```

### Turn 1 명령
```bash
echo "=== Qwen3-Coder turn1 ==="
QWEN_T5_TURN1=$(curl -s "$QWEN_URL/chat/completions" \
  -H "Authorization: Bearer $QWEN_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$QWEN_MODEL" --argjson p "$PAYLOAD_T5_TURN1" '$p + {model:$m}')")
echo "$QWEN_T5_TURN1" | jq '.choices[0].message | {content, tool_calls}'

echo "=== gpt-oss turn1 ==="
GPTOSS_T5_TURN1=$(curl -s "$GPTOSS_URL/chat/completions" \
  -H "Authorization: Bearer $GPTOSS_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$GPTOSS_MODEL" --argjson p "$PAYLOAD_T5_TURN1" '$p + {model:$m}')")
echo "$GPTOSS_T5_TURN1" | jq '.choices[0].message | {content, tool_calls}'
```

### Turn 1 예상 결과
- 양쪽 모두 `tool_calls[0].function.name == "list_users"`.
- 일부 모델(특히 병렬 지원 모델) 은 `list_users` + `create_user` 를 **한 번에** 호출할 수도 있으나,
  논리상 list 결과가 있어야 create 가 의미 있으므로 **순차 호출이 더 합리적**. 양상이 어떻게 갈리는지가 관찰 포인트.

### Turn 2 페이로드 (turn1 의 tool_call_id 를 직접 변수로 주입)

```bash
# turn1 응답에서 첫 tool_call 의 id 추출 (Qwen 예시; gpt-oss 도 동일)
QWEN_TC_ID=$(echo "$QWEN_T5_TURN1" | jq -r '.choices[0].message.tool_calls[0].id')
QWEN_TC_ARGS=$(echo "$QWEN_T5_TURN1" | jq -r '.choices[0].message.tool_calls[0].function.arguments')

# 가짜 tool 결과 (list_users 가 빈 목록 반환)
FAKE_LIST='[]'

PAYLOAD_T5_TURN2_QWEN=$(jq -nc \
  --argjson tools "$TOOLS_T5" \
  --arg tcid "$QWEN_TC_ID" \
  --arg tcargs "$QWEN_TC_ARGS" \
  --arg result "$FAKE_LIST" '{
  messages: [
    {role:"user",
     content:"List all current users, then create a new user named Alice with email alice@example.com."},
    {role:"assistant", content:null,
     tool_calls:[{id:$tcid, type:"function",
                  function:{name:"list_users", arguments:$tcargs}}]},
    {role:"tool", tool_call_id:$tcid, content:$result}
  ],
  tools: $tools,
  tool_choice:"auto"
}')

curl -s "$QWEN_URL/chat/completions" \
  -H "Authorization: Bearer $QWEN_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$QWEN_MODEL" --argjson p "$PAYLOAD_T5_TURN2_QWEN" '$p + {model:$m}')" \
  | jq '.choices[0].message | {content, tool_calls}'
```

> gpt-oss 도 동일 패턴(`GPTOSS_TC_ID`, `GPTOSS_TC_ARGS` 추출 후 turn2 구성).

### Turn 2 예상 결과
- 양쪽 모두 `tool_calls[0].function.name == "create_user"`,
  `arguments` 가 `{name:"Alice", email:"alice@example.com"}`.

### 실제 결과
- Qwen3-Coder:
  - turn1 tool_calls: ` `
  - turn2 tool_calls: ` `
  - 판정: ` `
- gpt-oss:
  - turn1 tool_calls: ` `
  - turn2 tool_calls: ` `
  - 판정: ` `

> **결과 해석 주의** (§0.4 와 함께 참조):
> - 본 케이스는 단일 호출이라 turn2 의 `tool_call_id` 가 turn1 id 와 정확히 일치하는지
>   여부와 무관하게 통과한다 (vLLM 이 강제하지 않음). PASS 라도 그게 message threading
>   적합성의 증거는 아님 — id routing 의미는 **T5d** 에서 직접 검증.
> - turn2 에 `create_user` 가 발행됐다는 사실만으로는 "모델이 turn1 결과를 읽고 분기했다"
>   가 입증되지 않음 — 무조건 create 를 부르는 모델이라도 같은 결과가 나옴. 분기 정합성은
>   **T5c** (negative control) 와 짝이 되어야 비로소 입증됨.

---

## T5b. Multi-turn 체인 — 조건문 변형 (turn1 단독 호출 강제)

### 입증 대상
- T5 의 prompt (`"... then create ..."`) 는 일부 모델에서 turn1 에 `list_users` + `create_user` 를
  **한 턴에 묶어** 호출할 여지가 있다 (parallel 지원 모델 또는 코딩 특화 모델일수록 그 경향).
- prompt 를 **조건문** 으로 바꾸면 (`"... and if Alice does not exist, create ..."`)
  모델은 `list_users` 결과를 **읽어야** create 여부를 결정할 수 있으므로
  turn1 에 `list_users` **단독 호출** 만 발생하는지 — 즉 multi-turn chain 검증의 안정화 prompt 변형이 성립하는지.

### 시나리오
- prompt: `"List all current users, and if Alice does not exist, create a new user named Alice with email alice@example.com."`
- 도구: T5 와 동일 (`list_users`, `create_user`).
- 기대 흐름: turn1 → `list_users` **단독** 호출 → 클라이언트가 가짜 결과(`[]`) 주입 →
  turn2 → `create_user({name:"Alice", email:"alice@example.com"})` 호출.

### Turn 1 페이로드 (T5 와 동일한 `TOOLS_T5` 재사용)
```bash
PAYLOAD_T5B_TURN1=$(jq -nc --argjson tools "$TOOLS_T5" '{
  messages: [{role:"user",
              content:"List all current users, and if Alice does not exist, create a new user named Alice with email alice@example.com."}],
  tools: $tools,
  tool_choice:"auto"
}')

echo "=== Qwen3-Coder turn1 ==="
chat "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "$PAYLOAD_T5B_TURN1"

echo "=== gpt-oss turn1 ==="
chat "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "$PAYLOAD_T5B_TURN1"
```

### Turn 1 예상 결과
- 양쪽 모두 `tool_calls` 길이 = **1**, `tool_calls[0].function.name == "list_users"`.
- T5 와 달리 turn1 에 `create_user` 가 함께 발행되면 안 됨 (조건 평가 전 create 는 논리적으로 불가).
- 만약 그래도 한 턴에 둘 다 호출되면 → 모델이 prompt 의 조건절을 무시한 것 → §10.3 시드/평가 prompt
  설계 시 "조건절도 안전하지 않다" 는 신호로 기록.

### Turn 2 (turn1 결과 = `[]` 주입 후)
- T5 와 동일한 패턴으로 turn2 페이로드 구성. 차이는 user 메시지가 조건문 prompt 라는 것 뿐.
- 예상: `tool_calls[0].function.name == "create_user"`, args = `{name:"Alice", email:"alice@example.com"}`.

### 실제 결과
- Qwen3-Coder:
  - turn1 tool_calls: `list_users` **단독** 1건 (예상 그대로)
  - turn2 tool_calls: `create_user({name:"Alice", email:"alice@example.com"})` 1건 (예상 그대로)
  - 판정: ✅ **PASS** — 조건문 prompt 가 turn1 단독 호출을 강제하고, turn2 에서 정확히 create_user 발행
- gpt-oss:
  - turn1 tool_calls: `list_users` **단독** 1건 (예상 그대로)
  - turn2 tool_calls: `create_user({name:"Alice", email:"alice@example.com"})` 1건 (예상 그대로)
  - 판정: ✅ **PASS** — 동일

> **시사점**: Phase 07 §6.2 실행 루프 검증을 위한 시드 prompt 는 "A 한 다음 B" 보다
> "**A 하고, 조건이면 B**" 형태로 짜는 편이 모델 간 거동 차이를 줄여 multi-turn chain 자체를
> 더 안정적으로 관찰할 수 있다. T6 (매크로 vs 원자) 처럼 **iterations** 자체가 비교 지표인 케이스에서는
> 반대로 조건문을 피해야 함 — 조건문은 iterations 를 인위적으로 늘리기 때문.

> **결과 해석 주의** (T5 와 동일): 단일 호출 케이스 → id routing 검증은 **T5d**, 분기 정합성
> 검증은 **T5c** 참조.

---

## T5c. Multi-turn 체인 — negative control (조건이 거짓이면 도구 미호출)

### 입증 대상
- T5b 의 결과 (turn1 = list_users, turn2 = create_user) 만으로는 **모델이 turn1 결과를
  읽고 분기했다는 증거가 되지 않는다** — turn2 에서 무조건 `create_user` 를 부르는 모델
  이라도 동일한 결과가 나오기 때문 (chain 의 절반만 입증).
- T5c 는 **반대 시나리오**: turn1 결과에 Alice 가 **이미 존재** 하도록 주입하고, 모델이
  `create_user` 를 **호출하지 않는** 지를 본다. T5b (Alice 부재 → create 호출) 와
  대조군으로 짝을 이뤄야 비로소 chain 검증이 성립.

### 시나리오
- prompt: T5b 와 동일 (`"List all current users, and if Alice does not exist, create a new user named Alice with email alice@example.com."`)
- turn1: `list_users` 단독 호출 (T5b 와 동일).
- turn1 fake result: 자연어 문장으로 "Alice 가 이미 존재" 를 표현.
- 기대 turn2: `tool_calls = null/[]`, `content` 자연어 ("Alice already exists" 류).

> **부수 발견 (Phase 07 시사점)**: 본 케이스 초기 실행 시 fake result 를 escape 된 JSON
> 배열 (`'[{"name":"Alice","email":"alice@example.com"}]'`) 로 넣었더니 **양 모델 모두
> 400 거절**. content 를 자연어 문자열 (`"The current user list contains one user: Alice
> with email alice@example.com."`) 로 바꾸자 정상 통과. 즉 vLLM (또는 chat template /
> tool-call-parser) 이 tool 메시지 content 의 escape 된 JSON 을 안전하게 처리하지 못하는
> 구간이 있다. **Phase 07 §6.2 의 tool result 직렬화 정책 결정 입력**: struct → JSON 문자열
> 박는 방식은 위험. 자연어 요약 또는 JSON 을 객체 한 단계 wrapping 으로 변환하는 게 안전.
> 이 발견 자체는 §10 (시드 도구) 의 result 포맷 가이드라인에 박을 가치가 있음.

### Turn 2 페이로드 (T5 패턴 재사용; `FAKE_LIST` 만 교체)

```bash
# 자연어 문자열로 — escape 된 JSON 배열은 400 거절 사유로 확인됨 (위 부수 발견 참조)
FAKE_LIST_T5C='The current user list contains one user: Alice with email alice@example.com.'

PAYLOAD_T5C_TURN2_QWEN=$(jq -nc \
  --argjson tools "$TOOLS_T5" \
  --arg tcid "$QWEN_TC_ID" \
  --arg tcargs "$QWEN_TC_ARGS" \
  --arg result "$FAKE_LIST_T5C" '{
  messages: [
    {role:"user",
     content:"List all current users, and if Alice does not exist, create a new user named Alice with email alice@example.com."},
    {role:"assistant", content:null,
     tool_calls:[{id:$tcid, type:"function",
                  function:{name:"list_users", arguments:$tcargs}}]},
    {role:"tool", tool_call_id:$tcid, content:$result}
  ],
  tools: $tools,
  tool_choice:"auto"
}')

curl -s "$QWEN_URL/chat/completions" \
  -H "Authorization: Bearer $QWEN_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$QWEN_MODEL" --argjson p "$PAYLOAD_T5C_TURN2_QWEN" '$p + {model:$m}')" \
  | jq '.choices[0].message | {content, tool_calls}'
```

> gpt-oss 도 동일 패턴 (`GPTOSS_TC_ID`, `GPTOSS_TC_ARGS` 사용).

### 예상 결과
- 양쪽 모두 turn2 `tool_calls = null/[]`, `content` 에 "Alice already exists" / "user already
  exists" / "no action needed" 류 자연어.
- turn2 에 `create_user` 가 호출되면 → 모델이 turn1 결과를 **읽지 않고** 무조건 create
  를 부르는 것 → **T5b 의 PASS 는 chain 증거로서 무효** (운 좋게 일치한 결과일 뿐).

### 실제 결과
- Qwen3-Coder:
  - turn2 tool_calls: `[]` (예상 그대로 — create_user 미발행)
  - turn2 content 발췌: "Alice 이미 존재해서 ..." 류 자연어
  - 판정: ✅ **PASS** — turn1 결과를 읽고 분기 (조건 거짓 → 도구 미호출)
- gpt-oss:
  - turn2 tool_calls: `[]` (예상 그대로 — create_user 미발행)
  - turn2 content 발췌: "Alice 이미 존재해서 ..." 류 자연어
  - 판정: ✅ **PASS** — 동일

> **T5b ✅ + T5c ✅ → chain 분기 정합성 입증 완료**. 즉 양 모델 모두 turn1 의 tool result 를
> **읽고** turn2 의사결정에 반영함. Phase 07 §6.2 outer-loop 가정 (모델이 결과 보고 분기) 성립.

> **다만 위 부수 발견 (escape 된 JSON content → 400) 은 별개 이슈로 §10.3 / §6.2 직렬화
> 정책에 반드시 반영해야 함** — 같은 함정에 운영 코드도 빠질 수 있음.

---

## T5d. tool_call_id routing 의미 검증 (parallel 결과 매칭)

### 입증 대상
- T5 에서 발견된 사실: vLLM 은 **단일 호출 케이스** 에서 엉터리 `tool_call_id` 를 거절하지
  않음 (wire-level 정합성 강제 X). 이건 §0.4 의 "vLLM stateless + id 는 cosmetic" 에 부합.
- 그러나 **parallel 호출** 에서는 의미가 달라질 수 있음 — 클라이언트가 turn2 에 보낸 tool
  메시지들을 모델이 어느 `tool_call` 의 결과로 인식하는가가 출력에 영향을 줄 가능성.
- chat template 이 id 를 prompt 에 렌더링한다면 → 모델이 id 로 routing → swap 시 출력 망가짐.
- chat template 이 id 를 무시하고 위치/내용만 렌더링한다면 → swap 무관 (cosmetic 확정).
- **본 케이스의 결과가 곧 운영 코드의 id 정확성 강제 필요 여부 결정**.

### 시나리오 (synthetic turn1 으로 양 모델 통일 비교)
- turn1 user: `"Compute (2+3) and (10+20) using the tool, in parallel."`
- turn1 assistant: **클라이언트가 직접 작성** 한 parallel tool_calls 메시지
  (`id="call_A"` for `add(2,3)`, `id="call_B"` for `add(10,20)`).
  - synthetic 으로 박는 이유: gpt-oss 는 T4 에서 sequential → 자연 발생 안 함. 양 모델을
    같은 history 로 평가해야 비교 가능.
  - id (`call_A`/`call_B`) 자체는 클라가 임의로 만든 값. §0.4 에 따라 vLLM 은 stateless
    이므로 "이전 completion 의 id 를 echo back 한 것" 과 결과 동등.

### 변형 3종

- **5d-A (정상 매칭)** — `tool_call_id="call_A" → "5"`, `tool_call_id="call_B" → "30"`.
- **5d-B (id swap, content 만 바꿈)** — `tool_call_id="call_A" → "30"`, `tool_call_id="call_B" → "5"`.
- **5d-C (garbage id)** — 두 tool 메시지 모두 `tool_call_id="bogus_xyz"`.

### 페이로드

```bash
TURN1_ASSISTANT_T5D=$(jq -nc '{
  role:"assistant", content:null,
  tool_calls:[
    {id:"call_A", type:"function",
     function:{name:"add", arguments:"{\"a\":2,\"b\":3}"}},
    {id:"call_B", type:"function",
     function:{name:"add", arguments:"{\"a\":10,\"b\":20}"}}
  ]
}')

TOOLS_T5D=$(jq -nc '[{
  type:"function",
  function:{
    name:"add",
    description:"Add two integers.",
    parameters:{type:"object",
      properties:{a:{type:"integer"}, b:{type:"integer"}},
      required:["a","b"]}
  }
}]')

build_t5d () {
  # $1 = id_for_first_result, $2 = content_for_first,
  # $3 = id_for_second_result, $4 = content_for_second
  jq -nc \
     --argjson assistant "$TURN1_ASSISTANT_T5D" \
     --argjson tools "$TOOLS_T5D" \
     --arg id1 "$1" --arg c1 "$2" \
     --arg id2 "$3" --arg c2 "$4" '{
    messages: [
      {role:"user", content:"Compute (2+3) and (10+20) using the tool, in parallel."},
      $assistant,
      {role:"tool", tool_call_id:$id1, content:$c1},
      {role:"tool", tool_call_id:$id2, content:$c2}
    ],
    tools: $tools,
    tool_choice:"auto"
  }'
}

PAYLOAD_T5D_A=$(build_t5d "call_A" "5"  "call_B" "30")
PAYLOAD_T5D_B=$(build_t5d "call_A" "30" "call_B" "5")
PAYLOAD_T5D_C=$(build_t5d "bogus_xyz" "5" "bogus_xyz" "30")
```

### 명령

```bash
for variant in A B C; do
  for endpoint in QWEN GPTOSS; do
    url_var="${endpoint}_URL"; key_var="${endpoint}_KEY"; model_var="${endpoint}_MODEL"
    payload_var="PAYLOAD_T5D_${variant}"
    echo "=== ${endpoint} 5d-${variant} ==="
    chat "${!url_var}" "${!key_var}" "${!model_var}" "${!payload_var}"
  done
done
```

### 판정 기준 (5d-B 가 핵심)

| 5d-B 응답 자연어 | 해석 | Phase 07 시사점 |
| :--- | :--- | :--- |
| "(2+3)=5, (10+20)=30" (정답) | 모델이 **id 무시**, content 의 위치/내용만 봄 | id 는 cosmetic 확정. echo back 은 §0.4 의 호환성 규약일 뿐 |
| "(2+3)=30, (10+20)=5" (swap 영향) | 모델이 **id 로 routing** | id 정확성 강제 필수. 잘못 매칭 시 silent corruption — §9 에 args 검증과 함께 id 매칭 검증 추가 |
| 혼란 / 재호출 / 사용자에게 질문 | 모델이 inconsistency 감지 | 가장 안전. Phase 07 트레이스에 inconsistency 신호 캡처 검토 |

5d-C 는 vLLM 의 wire-level 정합성 검증 강도 측정:
- 정상 응답 (tool 메시지 그대로 처리) → 정합성 검증 없음 (§0.4 가정 확정).
- 4xx 거절 → strict 모드 활성. 운영 코드는 id 정확성 강제 필수.

### 실제 결과
- Qwen3-Coder:
  - 5d-A 응답 발췌: ` `
  - 5d-B 응답 발췌: ` `
  - 5d-C 응답 / HTTP 상태: ` `
  - 판정: ` `
- gpt-oss:
  - 5d-A 응답 발췌: ` `
  - 5d-B 응답 발췌: ` `
  - 5d-C 응답 / HTTP 상태: ` `
  - 판정: ` `

> **결과 해석 가이드**: T5/T5b/T5c 의 단일 호출 케이스에서 엉터리 id 가 통과한 것은 vLLM 의
> 결함이 아닌 **정상 거동** 이며, T5d 가 그 이유 (parallel 이 아니면 id routing 단서가 필요
> 없음 + vLLM 은 stateless 라 검증할 원본 id 를 갖지 않음) 를 직접 보임.

---

## T6. 매크로 vs 원자 도구 (요구사항 §1 핵심 가설)

### 6.0 지표 정의 (iterations vs tool_calls 총수 — 사전 못박기)

본 케이스의 핵심 지표 두 가지가 자주 혼용되므로 분리해 정의한다. 이 둘을 같다고 가정하면
T4 의 결과(Qwen=parallel, gpt-oss=sequential) 와 결합 해석 시 결론이 흔들린다.

- **iterations** — 실행 루프의 **turn 수**. 사용자 prompt 한 번에 대해 (assistant ↔ tool
  result) 사이클이 몇 번 반복되어야 모델이 최종 응답에 도달하는가. Phase 07 §6.2 outer loop
  카운트.
- **tool_calls 총수** — 모든 turn 에 걸쳐 발행된 `tool_calls` 항목의 **합계**. Phase 07
  트레이스의 step 수.

두 지표가 갈리는 경우:
| 케이스 | iterations | tool_calls 총수 |
| :--- | :--- | :--- |
| parallel 모델 + 원자 도구 3개 호출 (T4 Qwen) | **1** | 3 |
| sequential 모델 + 원자 도구 3개 호출 (T4 gpt-oss) | **3** | 3 |
| 매크로 1회 호출 (이상적) | **1** | **1** |

§1 가설 "매크로가 iterations 를 줄인다" 의 **모델별 정확한 의미**:
- **sequential 모델**: macro 는 iterations 와 tool_calls 총수를 **둘 다** 감소.
- **parallel 모델**: iterations 는 이미 1 → macro 는 **tool_calls 총수만** 감소. iterations
  자체는 변화 없음. 따라서 §1 의 "iterations 감소" 표현은 parallel 모델 관점에서 부정확.

**본 T6 케이스의 실측 범위 한계**:
- 본 케이스는 turn 1 페이로드만 보내고 turn 2 이후는 시뮬레이션하지 않는다 → **iterations
  자체는 직접 측정하지 않음**. 측정 가능한 값은 **turn 1 tool_calls 길이** (= 첫 발행
  도구 수) 와 그 호출 이름의 macro 포함 여부.
- iterations 자체의 실측은 `tool` 메시지 주입까지 포함한 multi-turn 시뮬레이션이 필요.
  **T4b** 가 weather 시나리오에서 parallel(Qwen=iterations 1) / sequential(gpt-oss=iterations 3)
  iterations 차이를 **실측** 으로 입증 — §6.0 표의 가정을 사실로 격상.
- 단, T6 의 onboarding 시나리오는 도구 종류와 호출 패턴이 weather 와 다르므로 T4b 결과를
  바로 적용할 수 없음. T6 시나리오의 iterations 실측은 본 검증서 범위 밖 (Phase 07 본 구현
  트레이스에서 측정).
- 즉 T6 의 결과는 "**모델이 turn 1 부터 macro 를 선호하는가**" + "**그로 인해 turn 1
  tool_calls 길이가 줄어드는가**" 까지만 직접 입증. iterations 감소까지의 **함의** 는
  T4b 실측 + 6.0 표를 거쳐 추론한다.

### 입증 대상
- **같은 자연어 입력** 에 대해 도구 카탈로그를 (a) 원자만 / (b) 원자 + 매크로 로 바꿔 노출했을 때
  모델이 매크로를 **선호** 하는가 (turn 1 tool_calls 에 매크로 이름 포함 여부 + 길이 변화).
- 그 turn 1 변화가 6.0 의 모델별 매핑을 거쳐 **tool_calls 총수 감소** (양 모델 공통) 또는
  **iterations 감소** (sequential 모델 한정) 로 이어질 것이라는 §1 가설의 1차 근거 확보.
- Phase 07 의 운영 데모가 실제로 차이를 보일지 여부 — 시드 도구(§10.3) 를 미리 검증.

### 시나리오
- prompt: "Onboard a new team called Phoenix with members Alice (alice@x.com) and Bob (bob@x.com).
  Assign each to the engineering group, then send each a welcome message."

### 6.a — 원자 카탈로그 (3개)

```bash
TOOLS_T6_ATOMIC='[
  {"type":"function","function":{"name":"create_user",
    "description":"Create a user.",
    "parameters":{"type":"object","properties":{"name":{"type":"string"},"email":{"type":"string"}},"required":["name","email"]}}},
  {"type":"function","function":{"name":"assign_group",
    "description":"Assign a user to a group.",
    "parameters":{"type":"object","properties":{"user_id":{"type":"string"},"group":{"type":"string"}},"required":["user_id","group"]}}},
  {"type":"function","function":{"name":"send_welcome",
    "description":"Send a welcome message to a user.",
    "parameters":{"type":"object","properties":{"user_id":{"type":"string"}},"required":["user_id"]}}}
]'

PAYLOAD_T6_ATOMIC=$(jq -nc --argjson tools "$TOOLS_T6_ATOMIC" '{
  messages: [{role:"user",
    content:"Onboard a new team called Phoenix with members Alice (alice@x.com) and Bob (bob@x.com). Assign each to the engineering group, then send each a welcome message."}],
  tools: $tools,
  tool_choice:"auto",
  parallel_tool_calls: true
}')

chat "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "$PAYLOAD_T6_ATOMIC"
chat "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "$PAYLOAD_T6_ATOMIC"
```

### 6.b — 매크로 추가 카탈로그 (4개)

```bash
TOOLS_T6_MACRO=$(echo "$TOOLS_T6_ATOMIC" | jq '. + [
  {"type":"function","function":{"name":"onboard_new_team",
    "description":"Create users, assign them to a group, and send welcome messages — all in one call.",
    "parameters":{"type":"object",
      "properties":{
        "team":{"type":"string"},
        "group":{"type":"string"},
        "members":{"type":"array","items":{"type":"object",
          "properties":{"name":{"type":"string"},"email":{"type":"string"}},
          "required":["name","email"]}}
      },
      "required":["team","group","members"]}}}
]')

PAYLOAD_T6_MACRO=$(jq -nc --argjson tools "$TOOLS_T6_MACRO" '{
  messages: [{role:"user",
    content:"Onboard a new team called Phoenix with members Alice (alice@x.com) and Bob (bob@x.com). Assign each to the engineering group, then send each a welcome message."}],
  tools: $tools,
  tool_choice:"auto",
  parallel_tool_calls: true
}')

chat "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "$PAYLOAD_T6_MACRO"
chat "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "$PAYLOAD_T6_MACRO"
```

### 예상 결과
- **6.a (원자만)**: turn 1 tool_calls 길이가 **2~6 사이** (각 멤버에 대해 create/assign/welcome 분배).
  parallel 지원 모델은 한 turn 에 다 묶음 (길이 ↑, iterations=1), sequential 모델은 길이가
  작고 (예: 1~2) iterations 가 늘어나는 경향 (§6.0 표 참조).
- **6.b (매크로 추가)**: turn 1 tool_calls 길이 = **1**, name = `onboard_new_team`,
  `members` 배열에 두 명이 한 번에 들어감. → tool_calls 총수와 iterations 모두 1 (이상).
- **§1 가설 검증 포인트** (§6.0 정의 적용):
  - **tool_calls 총수 관점** (양 모델 공통): 6.b 의 turn 1 길이 ≤ 6.a 의 turn 1 길이.
    작으면 가설 직접 지지.
  - **iterations 관점** (sequential 모델 한정 — gpt-oss): 6.b 의 turn 1 단일 호출 → 1
    iteration 으로 종료 가능. 6.a 는 sequential 이라 iterations 가 멤버 수 × 단계 수에
    비례해 늘어남. parallel 모델 (Qwen) 은 이 축에서 차이 없음 (양쪽 다 1).
- 모델 간 차이: 양쪽 모두 macro 를 선호하면 가설은 약화 — Phase 07 비교는 "약한 모델" 의
  어려운 케이스를 더 추가해야 의미를 갖는다는 신호.

### 실제 결과
(아래 모든 길이는 **turn 1** 의 `tool_calls` 길이 — §6.0 의 한계 항목 참조)

- Qwen3-Coder (parallel — §6.0 에 따라 tool_calls 총수 관점만 비교 가능):
  - 6.a turn 1 tool_calls 길이: ` ` / 호출 이름들: ` `
  - 6.b turn 1 tool_calls 길이: ` ` / 호출 이름들 (macro 포함 여부): ` `
  - tool_calls 총수 감소 폭 (6.a − 6.b): ` `
  - 코멘트: ` `
- gpt-oss (sequential — §6.0 에 따라 두 관점 모두 함의 추론 가능):
  - 6.a turn 1 tool_calls 길이: ` ` / 호출 이름들: ` `
  - 6.b turn 1 tool_calls 길이: ` ` / 호출 이름들 (macro 포함 여부): ` `
  - 6.a 가 turn 1 에 단일 호출이면 iterations 가 멤버 수 × 단계에 비례해 늘 것 — 함의 코멘트: ` `
  - 코멘트: ` `

---

## T7. `reasoning_content` 노출 여부

### 입증 대상
- 요구사항 §6.4 의 가정 — gpt-oss 는 `reasoning_content` 별도 필드, Qwen3-Coder 는 본문 인라인 — 이
  실제로 그러한지.
- Phase 07 트레이스의 `steps[*].reasoning` 캡처 로직이 어느 모델에서 데이터가 채워지는지 확정.

### 페이로드
```bash
PAYLOAD_T7=$(jq -nc '{
  messages: [{role:"user",
    content:"Carefully add 17 and 28. Think step by step, then call the tool with the answer."}],
  tools: [{
    type:"function",
    function:{
      name:"submit_answer",
      description:"Submit the final integer answer.",
      parameters:{type:"object",properties:{value:{type:"integer"}},required:["value"]}
    }
  }],
  tool_choice:"auto"
}')
```

### 명령 — 응답 전체를 찍어 reasoning_content 필드를 직접 확인
```bash
echo "=== Qwen3-Coder ==="
curl -s "$QWEN_URL/chat/completions" \
  -H "Authorization: Bearer $QWEN_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$QWEN_MODEL" --argjson p "$PAYLOAD_T7" '$p + {model:$m}')" \
  | jq '.choices[0].message'

echo "=== gpt-oss ==="
curl -s "$GPTOSS_URL/chat/completions" \
  -H "Authorization: Bearer $GPTOSS_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$GPTOSS_MODEL" --argjson p "$PAYLOAD_T7" '$p + {model:$m}')" \
  | jq '.choices[0].message'
```

### 예상 결과
- **gpt-oss**: 응답 객체에 `reasoning_content` 키 존재(추론 단계 자연어), `content` 는 짧거나 null,
  `tool_calls` 에 `submit_answer({value:45})`.
- **Qwen3-Coder**:
  - 시나리오 A: `content` 에 `<think>...</think>` 블록이 보이고 그 뒤에 본문/도구 호출.
  - 시나리오 B: `content` 가 그냥 자연어 + `tool_calls`. (Coder 변종은 think 출력 안 하는 경우 있음)
  - **`reasoning_content` 키 자체는 존재하지 않거나 `null`** — 이게 §6.4 의 "Qwen3 는 본문 인라인" 가정 그대로.

### 실제 결과
- Qwen3-Coder:
  - `reasoning_content` 존재 여부: ` `
  - `content` 안 `<think>` 블록 유무: ` `
  - tool_calls submit_answer.value: ` `
  - 판정: ` `
- gpt-oss:
  - `reasoning_content` 존재 여부: ` `
  - `reasoning_content` 길이(대략): ` `
  - tool_calls submit_answer.value: ` `
  - 판정: ` `

---

## T8. 인자 스키마 준수 — required 필드 누락 시 거동

### 입증 대상
- 모델이 `required` 인자를 누락한 채로 도구를 호출할 가능성. vLLM 은 OpenAI 의 `strict:true` 가
  없으므로(요구사항 §4.3 코멘트), 인자 검증을 모델 능력에 의존한다.
- Phase 07 §9.2 의 `returns` 검증과 별개로 **`parameters` 검증 추가가 필요한지** 결정하는 데이터.

### 페이로드 (이메일 의도적 누락)
```bash
PAYLOAD_T8=$(jq -nc '{
  messages: [{role:"user", content:"Create a user named Alice."}],
  tools: [{
    type:"function",
    function:{
      name:"create_user",
      description:"Create a new user. Both name and email are required.",
      parameters:{type:"object",
        properties:{name:{type:"string"}, email:{type:"string", format:"email"}},
        required:["name","email"]}
    }
  }],
  tool_choice:"auto"
}')
```

### 명령
```bash
chat "$QWEN_URL"   "$QWEN_KEY"   "$QWEN_MODEL"   "$PAYLOAD_T8"
chat "$GPTOSS_URL" "$GPTOSS_KEY" "$GPTOSS_MODEL" "$PAYLOAD_T8"
```

### 예상 결과 (어느 쪽이 나와도 의미 있음)
- **A**: 모델이 자연어로 "이메일 주소가 필요합니다, 알려주세요" 하고 도구는 안 부름 (`tool_calls=null`). → 가장 안전.
- **B**: 모델이 임의 이메일을 만들어 호출 (예: `alice@example.com`). → Phase 07 에서는 `args` 검증 추가 필요성 시사 X (LLM 이 알아서 채움) 하지만 **할루시 데이터** 위험.
- **C**: 모델이 `email` 누락한 채 호출 (`{name:"Alice"}`). → **§9 보안 외, parameters 검증 추가 필수**.

### 실제 결과
- Qwen3-Coder:
  - 거동(A/B/C): ` `
  - tool_calls.arguments: ` `
  - 판정: ` `
- gpt-oss:
  - 거동(A/B/C): ` `
  - tool_calls.arguments: ` `
  - 판정: ` `

---

## T9. 누수 감지 자가 검증 (negative test)

### 입증 대상
- Phase 07 §4.3.3 의 누수 감지 로직 패턴이 **실제 raw 마크업과 매칭** 되는지 — 패턴 자체의 적합성 검증.
- 단, 본 케이스는 vLLM 이 **정상 작동** 중인 환경에서는 자연스럽게 재현되지 않는다.
  대신 raw 마크업이 실제로 어떤 모양인지 확인하기 위해 **`tools` 를 빼고** 같은 prompt 를 던져,
  모델이 자유롭게 출력한 형식과 §4.3.3 패턴이 일치하는지 본다.

### 페이로드
```bash
PAYLOAD_T9=$(jq -nc '{
  messages: [
    {role:"system",
     content:"You have access to a tool named add(a:int,b:int). Call it using your native tool-call format."},
    {role:"user", content:"Add 7 and 13."}
  ]
}')
# 의도적으로 tools 필드 미포함 → 파서가 작동하지 않아 raw 출력이 그대로 노출됨
```

### 명령
```bash
echo "=== Qwen3-Coder raw ==="
curl -s "$QWEN_URL/chat/completions" \
  -H "Authorization: Bearer $QWEN_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$QWEN_MODEL" --argjson p "$PAYLOAD_T9" '$p + {model:$m}')" \
  | jq -r '.choices[0].message.content'

echo "=== gpt-oss raw ==="
curl -s "$GPTOSS_URL/chat/completions" \
  -H "Authorization: Bearer $GPTOSS_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$GPTOSS_MODEL" --argjson p "$PAYLOAD_T9" '$p + {model:$m}')" \
  | jq -r '.choices[0].message.content'
```

### 예상 결과
- Qwen3-Coder: `<tool_call>` 태그 또는 `<function=add>` 류 XML 마크업이 본문에 포함될 가능성.
- gpt-oss: harmony 채널 토큰(`<|channel|>...<|message|>...`) 또는 ` ```json\n{"name":"add"...} ` 류 마크업.
- 본문에 위 패턴이 보이면 §4.3.3 의 누수 감지 정규식과 매칭 가능성 확인 → 패턴 적합성 OK.

### 실제 결과
- Qwen3-Coder content 발췌: ` `
- gpt-oss content 발췌: ` `
- §4.3.3 패턴(`<tool_call>`, `<\|tool▁call▁begin\|>`, ` ```json{"name"`, `<function_call>`) 중 매칭된 것: ` `
- 판정 (패턴 보강 필요?): ` `

---

## T10. 토큰 사용량 / 응답 시간 베이스라인

### 입증 대상
- Phase 07 §6.3 의 `input_tokens`, `output_tokens`, `latency_ms` 지표가 vLLM 응답에 그대로 들어오는지.
- Grafana 대시보드(§12) 패널의 데이터 소스가 보장되는지.

### 명령 (T2 페이로드 재사용, `usage` 필드까지 출력)
```bash
echo "=== Qwen3-Coder usage ==="
time curl -s "$QWEN_URL/chat/completions" \
  -H "Authorization: Bearer $QWEN_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$QWEN_MODEL" --argjson p "$PAYLOAD_T2" '$p + {model:$m}')" \
  | jq '.usage'

echo "=== gpt-oss usage ==="
time curl -s "$GPTOSS_URL/chat/completions" \
  -H "Authorization: Bearer $GPTOSS_KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$GPTOSS_MODEL" --argjson p "$PAYLOAD_T2" '$p + {model:$m}')" \
  | jq '.usage'
```

### 예상 결과
- 양쪽 응답에 `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens` 존재.
- gpt-oss 일부 vLLM 빌드는 reasoning 토큰을 별도(`completion_tokens_details.reasoning_tokens`) 로 노출.
  이 경우 Phase 07 의 `output_tokens` 정의를 "completion_tokens 합계" 로 명확화 필요.
- `time` 출력으로 단일 호출 latency 의 대략적 감각.

### 실제 결과
- Qwen3-Coder usage: ` ` / 체감 latency: ` `
- gpt-oss usage: ` ` (reasoning_tokens 별도 존재 여부: ` `) / 체감 latency: ` `
- 판정: ` `

---

## 종합 판정 체크리스트

테스트 완료 후 아래를 채운다 — 이 결과가 곧 Phase 07 설계 착수 조건.

| 항목 | Qwen3-Coder | gpt-oss | Phase 07 설계 시사점 |
| :--- | :--- | :--- | :--- |
| T2 파서 정상 동작 | ` ` | ` ` | 둘 다 ✅ 여야 §4.3 가정 성립 |
| T3 도구 미사용 판단 | ` ` | ` ` | ❌ 시 §10.2 라벨 재고 |
| T4 parallel 지원 (turn 1 tool_calls 길이) | ` ` | ` ` | 부분 시 §6.2 inner-loop 의미 약화 — 트레이스 라벨로만 활용 |
| T4b iteration 실측 (도달 iterations) | ` ` (기대 2) | ` ` (기대 4) | gpt-oss turn 2 = null 시 §6.2 outer-loop 무의미. 무한 루프 시 max_iterations 가드 우선 |
| T5 multi-turn chain (turn2 발행) | ✅ (T5b 변형으로 검증) | ✅ (T5b 변형으로 검증) | T5c 와 함께 ✅ → §6.2 outer-loop 가정 성립 |
| T5c chain 분기 정합성 (negative control) | ✅ | ✅ | T5b 와 함께 ✅ → 모델이 turn1 결과를 읽고 분기함이 입증됨 |
| T5d id routing 의미 (5d-B 응답) | ` ` (cosmetic / routing / 혼란) | ` ` (cosmetic / routing / 혼란) | routing → §9 에 id 매칭 검증 추가 / cosmetic → echo back 은 호환성 규약으로 한정 (§0.4) |
| T6 macro 선호 (turn 1 tool_calls 길이) | ` ` (parallel: 총수↓ 만 평가) | ` ` (sequential: 총수↓ + iterations↓ 함의) | 양쪽 ✅ 시 §10.3 시드에 더 어려운 케이스 추가 필요. iterations 자체의 직접 측정은 §6.0 한계로 본 구현 트레이스에서 수행 |
| T7 reasoning_content 채널 | ` ` (기대: 없음/인라인) | ` ` (기대: 별도 필드) | §6.4 캡처 로직 확정 |
| T8 required 필드 거동 | ` ` | ` ` | C 케이스 발생 시 §9 에 `parameters` 검증 추가 |
| T9 누수 패턴 적합성 | ` ` | ` ` | 매칭 X 시 §4.3.3 정규식 보강 |
| T10 usage 메트릭 | ` ` | ` ` | reasoning_tokens 별도 시 §6.3 정의 명확화 |

### 이슈 발견 시 후속 액션
- **T2 ❌** → 운영자 측 vLLM 기동 옵션 점검 (`--enable-auto-tool-choice --tool-call-parser <X>`).
  설계 진행 보류.
- **T4b gpt-oss turn 2 = null** (Seoul 만 답하고 종료) → 모델이 turn 1 결과만으로 종료 →
  §6.2 outer-loop 가 gpt-oss 에서 무의미 → prompt/system 메시지에서 "모든 도시 답해라"
  강제 또는 §10.2 라벨링 재검토.
- **T4b 무한 루프 / 같은 도구 반복** → §6.2 max_iterations 가드 즉시 구현 우선.
- **T5c ❌ (turn2 에 create_user 호출)** → 모델이 turn1 결과를 읽지 않고 무조건 다음 도구를
  호출하는 것 → §6.2 루프의 분기 가정 재검토. 시드 prompt 를 더 명시적인 조건절/검증절로
  재설계하거나, 모델 능력 한계로 분류해 §10.2 라벨링에 반영.
- **(T5c 부수 발견) tool 메시지 content 에 escape 된 JSON 배열 → 400** → §6.2 의 ToolMessage
  builder 와 §10.3 시드 도구의 result 직렬화 정책에 반영: tool content 는 **(a) 자연어 요약**
  또는 **(b) 객체 한 단계 wrapping** 으로 보낼 것. JSON 배열을 string 으로 그대로 박지 말 것.
  운영 코드가 같은 함정에 빠질 수 있으므로 정책 명문화 필요.
- **T5d-B 가 swap 영향** (응답이 "(2+3)=30, (10+20)=5") → 모델이 id 로 routing → §9 에 turn2
  페이로드 빌더에서 id 매칭 검증 자동화 추가. 잘못 매칭 시 silent corruption 위험.
- **T5d-B 가 cosmetic** (응답 정답) → §0.4 의 "id 는 cosmetic" 가정 확정. echo back 은
  LangChain 자동처리에 위임, Phase 07 코드 차원의 추가 검증 불필요.
- **T5d-C 4xx 거절** → strict 모드 활성. 운영 코드에 id 정확성 강제 + 클라가 임의 id 생성
  하는 코드 경로 금지.
- **T6 동일 결과** → §10.3 시드에 5+ 단계 워크플로 또는 작은 모델 슬롯 추가를 P07 본 단계 범위에 포함.
- **T7 gpt-oss 에서 reasoning_content 부재** → vLLM 버전 확인 후 langchain-openai 의 매핑 경로 재검토.
- **T8 C 케이스 발생** → §9 에 `parameters` JSON Schema 로 args 사전 검증 단계 추가 (요구사항 보강).
