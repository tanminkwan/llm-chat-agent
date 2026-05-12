"""Unit tests for ``libs.toollab.sandbox``.

Covers AST whitelist, signature matching, runtime isolation, timeouts, and
return-schema validation. Reference: docs/P07_설계서.md §4.
"""
from __future__ import annotations

import time

import pytest

from libs.toollab import sandbox
from libs.toollab.sandbox import (
    ToolDefinitionError,
    compile_handler,
    run_handler_with_timeout,
    validate_code_ast,
    validate_jsonschema,
    validate_return,
)


# ---------------------------------------------------------------------------
# AST whitelist
# ---------------------------------------------------------------------------


class TestAstWhitelist:
    def test_clean_code_passes(self):
        code = "def handler(a, b):\n    return a + b\n"
        assert validate_code_ast(code) == []

    def test_import_rejected(self):
        v = validate_code_ast("import os\ndef handler():\n    return 1\n")
        # Either denied_node:Import or unknown_node:alias — first one is what
        # surfaces to the user.
        assert any(item.kind == "denied_node" and item.detail == "Import" for item in v)

    def test_from_import_rejected(self):
        v = validate_code_ast("from os import getcwd\ndef handler():\n    return 1\n")
        assert any(item.kind == "denied_node" and item.detail == "ImportFrom" for item in v)

    def test_dunder_attr_rejected(self):
        code = (
            "def handler():\n"
            "    return ().__class__.__bases__\n"
        )
        v = validate_code_ast(code)
        kinds = {(item.kind, item.detail) for item in v}
        assert ("denied_attr", "__class__") in kinds
        assert ("denied_attr", "__bases__") in kinds

    def test_eval_call_rejected(self):
        v = validate_code_ast(
            "def handler():\n    return eval('1+1')\n"
        )
        assert any(item.kind == "denied_call" and item.detail == "eval" for item in v)

    def test_open_call_rejected(self):
        v = validate_code_ast(
            "def handler():\n    return open('/etc/passwd').read()\n"
        )
        assert any(item.kind == "denied_call" and item.detail == "open" for item in v)

    def test_getattr_call_rejected(self):
        v = validate_code_ast(
            "def handler():\n    return getattr(1, '__class__')\n"
        )
        # Both the `getattr` call AND the `__class__` literal-as-string are
        # caught — only call-site is what surfaces as a hard error.
        assert any(item.kind == "denied_call" and item.detail == "getattr" for item in v)

    def test_async_rejected(self):
        v = validate_code_ast(
            "async def handler():\n    return 1\n"
        )
        assert any(item.kind == "denied_node" for item in v)

    def test_nested_function_allowed(self):
        code = (
            "def handler(xs):\n"
            "    def double(x):\n"
            "        return x * 2\n"
            "    return [double(x) for x in xs]\n"
        )
        assert validate_code_ast(code) == []


# ---------------------------------------------------------------------------
# Signature matching
# ---------------------------------------------------------------------------


class TestSignatureMatching:
    def test_handler_missing(self):
        with pytest.raises(ToolDefinitionError) as ei:
            compile_handler("x = 1\n", {"type": "object", "properties": {}})
        assert ei.value.kind == "handler_not_defined"

    def test_args_extra_in_handler(self):
        params = {"type": "object", "properties": {"a": {"type": "integer"}},
                  "required": ["a"]}
        with pytest.raises(ToolDefinitionError) as ei:
            compile_handler("def handler(a, b):\n    return a + b\n", params)
        assert ei.value.kind == "args_mismatch"

    def test_args_missing_in_handler(self):
        params = {"type": "object",
                  "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                  "required": ["a", "b"]}
        with pytest.raises(ToolDefinitionError):
            compile_handler("def handler(a):\n    return a\n", params)

    def test_args_match_succeeds(self):
        params = {"type": "object",
                  "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                  "required": ["a", "b"]}
        h = compile_handler("def handler(a, b):\n    return {'sum': a + b}\n", params)
        assert h(a=2, b=3) == {"sum": 5}

    def test_optional_param_default_allowed(self):
        params = {"type": "object",
                  "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                  "required": ["a"]}
        h = compile_handler(
            "def handler(a, b=10):\n    return {'sum': a + b}\n", params
        )
        assert h(a=1) == {"sum": 11}


# ---------------------------------------------------------------------------
# Sandbox isolation
# ---------------------------------------------------------------------------


class TestSandboxIsolation:
    def test_no_unauthorised_module_access(self):
        # `os` is not in INJECTED_MODULES, so direct reference is NameError at
        # runtime (AST already blocks `import os`).
        params = {"type": "object", "properties": {}}
        h = compile_handler(
            "def handler():\n    return os.getcwd()\n", params
        )
        with pytest.raises(NameError):
            h()

    def test_injected_modules_available(self):
        params = {"type": "object", "properties": {}}
        h = compile_handler(
            "def handler():\n"
            "    return {'pi': math.pi, 'mean': statistics.mean([1,2,3])}\n",
            params,
        )
        out = h()
        assert out["pi"] > 3.14
        assert out["mean"] == 2

    def test_safe_print_is_noop(self):
        """User code with print() must not crash and must not produce output."""
        params = {"type": "object", "properties": {}}
        h = compile_handler(
            "def handler():\n"
            "    print('hi')\n"
            "    return {'ok': True}\n",
            params,
        )
        assert h() == {"ok": True}


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_handler_returns_within_budget(self):
        def fast():
            return {"v": 42}

        res = run_handler_with_timeout(fast, {}, timeout_ms=500)
        assert res["ok"] is True
        assert res["result"] == {"v": 42}
        assert res["latency_ms"] >= 0

    def test_handler_exceeds_budget(self):
        def slow():
            time.sleep(2)
            return "never returned"

        res = run_handler_with_timeout(slow, {}, timeout_ms=200)
        assert res["ok"] is False
        assert res["error_kind"] == "timeout"

    def test_handler_exception_is_caught(self):
        def boom():
            raise ValueError("kaboom")

        res = run_handler_with_timeout(boom, {}, timeout_ms=200)
        assert res["ok"] is False
        assert res["error_kind"] == "exception"
        assert "ValueError" in res["error"]


# ---------------------------------------------------------------------------
# Return / schema validation
# ---------------------------------------------------------------------------


class TestReturnValidation:
    def test_returns_match_passes(self):
        schema = {"type": "object",
                  "properties": {"v": {"type": "integer"}},
                  "required": ["v"]}
        assert validate_return({"v": 1}, schema) is None

    def test_returns_mismatch_returns_error(self):
        schema = {"type": "object",
                  "properties": {"v": {"type": "integer"}},
                  "required": ["v"]}
        err = validate_return({"v": "not-an-int"}, schema)
        assert err is not None
        assert err["error_kind"] == "return_schema_invalid"

    def test_invalid_jsonschema_doc(self):
        msg = validate_jsonschema({"type": "not-a-real-type"})
        assert msg is not None and msg

    def test_valid_jsonschema_doc(self):
        assert validate_jsonschema({"type": "object", "properties": {}}) is None
