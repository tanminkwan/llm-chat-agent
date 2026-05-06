"""
LLM_LOG 표준 emitter 와 보강 유틸리티.

Phase 06 (Observability) 요구에 따라 모든 [LLM_LOG] 라인은
- ISO 8601 UTC timestamp
- type
- 그 외 type 별 필드
를 포함해야 한다. 본 헬퍼를 통해 호출 지점에서는 비즈니스 페이로드만 만들고
timestamp 부착은 자동으로 일어나도록 한다.
"""
import datetime
import json
import logging
from collections.abc import Mapping
from typing import Any, Optional


_logger = logging.getLogger("llm-chat-agent")


def now_iso() -> str:
    """현재 UTC 시각의 ISO 8601 문자열."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def emit_llm_log(level: str, payload: Mapping[str, Any]) -> None:
    """LLM_LOG 표준 emitter.

    - payload 에 timestamp 가 없으면 자동으로 부착한다.
    - level == "error" 면 logger.error, 그 외에는 logger.debug 로 기록한다.
    """
    enriched = dict(payload)
    enriched.setdefault("timestamp", now_iso())

    line = f"[LLM_LOG] {json.dumps(enriched, ensure_ascii=False)}"
    if level == "error":
        _logger.error(line)
    else:
        _logger.debug(line)


def _as_mapping(value: Any) -> Mapping:
    """Mapping (dict 등) 만 통과시키고 그 외는 빈 dict 로 치환.

    MagicMock 같은 객체가 들어와도 안전하게 빈 dict 로 다뤄 JSON 직렬화 실패를 방지한다.
    """
    return value if isinstance(value, Mapping) else {}


def extract_usage(final_chunk: Any) -> dict:
    """langchain chunk 의 usage_metadata / response_metadata 에서 token·model 추출.

    모델 / 게이트웨이에 따라 일부 필드가 누락될 수 있으므로 모두 Optional 로 처리한다.
    반환 dict 의 키: input_tokens, output_tokens, model
    """
    if final_chunk is None:
        return {"input_tokens": None, "output_tokens": None, "model": None}

    usage_metadata = _as_mapping(getattr(final_chunk, "usage_metadata", None))
    response_metadata = _as_mapping(getattr(final_chunk, "response_metadata", None))

    model = response_metadata.get("model_name") or response_metadata.get("model")
    if not isinstance(model, (str, type(None))):
        model = None

    input_tokens = usage_metadata.get("input_tokens")
    output_tokens = usage_metadata.get("output_tokens")
    if not isinstance(input_tokens, (int, type(None))):
        input_tokens = None
    if not isinstance(output_tokens, (int, type(None))):
        output_tokens = None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model,
    }


def rag_score_summary(results: list) -> dict:
    """RAG 검색 결과 list 에서 top_score / min_score 를 계산."""
    scores = [r.get("score") for r in results if r.get("score") is not None]
    if not scores:
        return {"top_score": None, "min_score": None}
    return {"top_score": max(scores), "min_score": min(scores)}
