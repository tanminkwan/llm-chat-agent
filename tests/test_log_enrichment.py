"""
test_log_enrichment.py - Phase 06 로그 보강 헬퍼 단위 테스트

- emit_llm_log: timestamp 자동 부착 + 레벨별 logger 분기
- extract_usage: chunk 의 usage_metadata / response_metadata 에서 토큰·모델 추출
- rag_score_summary: results 리스트로부터 top_score / min_score 산출
"""
import json
import logging
import re
from unittest.mock import MagicMock

import pytest

from libs.core.logging_helpers import emit_llm_log, extract_usage, rag_score_summary, now_iso


# --- emit_llm_log -------------------------------------------------------------

def _capture(caplog):
    """caplog 의 마지막 [LLM_LOG] 라인의 JSON 본문을 dict 로 반환."""
    lines = [r.getMessage() for r in caplog.records if "[LLM_LOG]" in r.getMessage()]
    assert lines, "no [LLM_LOG] line captured"
    payload = re.search(r"\[LLM_LOG\]\s+(\{.*\})", lines[-1]).group(1)
    return json.loads(payload), caplog.records[-1].levelno


class TestEmitLLMLog:
    def test_auto_timestamp(self, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        emit_llm_log("debug", {"type": "request", "user_id": "u1"})
        data, level = _capture(caplog)
        assert data["type"] == "request"
        assert data["user_id"] == "u1"
        # ISO 8601 (UTC 기준 +00:00)
        assert "timestamp" in data
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", data["timestamp"])
        assert level == logging.DEBUG

    def test_error_level(self, caplog):
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        emit_llm_log("error", {"type": "error", "error": "boom"})
        _, level = _capture(caplog)
        assert level == logging.ERROR

    def test_explicit_timestamp_preserved(self, caplog):
        """호출자가 timestamp 를 직접 넣었으면 덮어쓰지 않는다."""
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        ts = "2026-01-01T00:00:00+00:00"
        emit_llm_log("debug", {"type": "request", "timestamp": ts})
        data, _ = _capture(caplog)
        assert data["timestamp"] == ts

    def test_korean_unescaped(self, caplog):
        """ensure_ascii=False 로 한글이 그대로 적재되어야 한다."""
        caplog.set_level(logging.DEBUG, logger="llm-chat-agent")
        emit_llm_log("debug", {"type": "request", "query": "한글 검색어"})
        data, _ = _capture(caplog)
        assert data["query"] == "한글 검색어"


# --- extract_usage ------------------------------------------------------------

class TestExtractUsage:
    def test_full_metadata(self):
        chunk = MagicMock()
        chunk.usage_metadata = {"input_tokens": 12, "output_tokens": 34}
        chunk.response_metadata = {"model_name": "gpt-4o-mini"}
        result = extract_usage(chunk)
        assert result == {"input_tokens": 12, "output_tokens": 34, "model": "gpt-4o-mini"}

    def test_alternate_model_key(self):
        """response_metadata 가 model_name 대신 model 키를 쓰는 경우도 처리"""
        chunk = MagicMock()
        chunk.usage_metadata = {"input_tokens": 1, "output_tokens": 2}
        chunk.response_metadata = {"model": "fallback-model"}
        result = extract_usage(chunk)
        assert result["model"] == "fallback-model"

    def test_missing_usage(self):
        chunk = MagicMock()
        chunk.usage_metadata = None
        chunk.response_metadata = None
        result = extract_usage(chunk)
        assert result == {"input_tokens": None, "output_tokens": None, "model": None}

    def test_none_chunk(self):
        result = extract_usage(None)
        assert result == {"input_tokens": None, "output_tokens": None, "model": None}


# --- rag_score_summary --------------------------------------------------------

class TestRagScoreSummary:
    def test_typical_scores(self):
        results = [{"score": 0.7}, {"score": 0.95}, {"score": 0.5}]
        assert rag_score_summary(results) == {"top_score": 0.95, "min_score": 0.5}

    def test_empty(self):
        assert rag_score_summary([]) == {"top_score": None, "min_score": None}

    def test_skips_none_scores(self):
        results = [{"score": None}, {"score": 0.3}]
        assert rag_score_summary(results) == {"top_score": 0.3, "min_score": 0.3}

    def test_all_none(self):
        results = [{"score": None}, {"score": None}]
        assert rag_score_summary(results) == {"top_score": None, "min_score": None}


# --- now_iso ------------------------------------------------------------------

class TestNowIso:
    def test_iso_format(self):
        ts = now_iso()
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)
        # timezone 정보 포함 (+00:00 또는 Z)
        assert ts.endswith("+00:00") or ts.endswith("Z")
