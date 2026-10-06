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
