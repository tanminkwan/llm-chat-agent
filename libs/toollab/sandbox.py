"""Tool Lab sandbox — AST validation + isolated exec + timeout + return validation.

Reference: docs/P07_설계서.md §4.

Three security layers (none guarantees 100% safety — UI access is gated to
``TOOLLAB_ALLOWED_GROUPS`` as a 1st-line defense):

1. **Static (AST whitelist)** — :func:`validate_code_ast` rejects ``import``,
   dunder bypass, and dangerous builtins at registration time.
2. **Runtime (isolated namespace)** — :func:`build_sandbox_globals` provides only
   a curated set of builtins and a fixed list of pre-injected modules.
3. **Timeout** — :func:`run_handler_with_timeout` runs the handler in a worker
   thread and returns ``timeout`` immediately when the deadline elapses (the
   thread itself cannot be force-terminated; CPU-bound code keeps running until
   it returns).
"""
from __future__ import annotations

import ast
import datetime as _dt
import inspect
import json
import math
import random
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeoutError
from dataclasses import dataclass
from typing import Any, Callable, Optional

import jsonschema

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ToolDefinitionError(Exception):
    """Raised at registration time (AST/signature/schema fail)."""

    def __init__(self, kind: str, detail: str = "", line: int | None = None,
                 col: int | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.line = line
        self.col = col


class ToolExecutionError(Exception):
    """Raised at execution time (timeout/exception/return-schema-invalid)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------------------
# AST whitelist
# ---------------------------------------------------------------------------

ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset({
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr,
    ast.If, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass,
    ast.Tuple, ast.List, ast.Dict, ast.Set,
    ast.Subscript, ast.Slice, ast.Index if hasattr(ast, "Index") else ast.Subscript,
    ast.Attribute, ast.Call, ast.Name, ast.Constant,
    ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare, ast.IfExp,
    ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.comprehension,
    ast.Try, ast.Raise, ast.ExceptHandler, ast.ClassDef, ast.Lambda,
    ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.Invert,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.FloorDiv,
    ast.LShift, ast.RShift, ast.BitOr, ast.BitXor, ast.BitAnd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.Load, ast.Store, ast.Del,
    ast.keyword, ast.Starred,
    ast.JoinedStr, ast.FormattedValue,  # f-string
})

DENIED_NODES: frozenset[type[ast.AST]] = frozenset({
    ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.With, ast.AsyncWith,
    ast.AsyncFunctionDef, ast.Await, ast.AsyncFor,
    ast.Yield, ast.YieldFrom,
})

DENIED_ATTR_PREFIXES: tuple[str, ...] = (
    "__class__", "__bases__", "__subclasses__", "__globals__", "__dict__",
    "__import__", "__builtins__", "__getattribute__", "__getattr__",
    "__setattr__", "__delattr__", "__mro__", "__init_subclass__",
    "__code__", "__closure__", "__func__", "__self__",
    "func_globals", "f_globals", "f_locals", "f_code", "f_back",
    "gi_frame", "cr_frame", "ag_frame",
)

DENIED_CALL_NAMES: frozenset[str] = frozenset({
    "eval", "exec", "compile", "open", "input", "__import__",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "memoryview", "breakpoint", "help", "quit", "exit",
})


@dataclass
class AstViolation:
    line: int
    col: int
    kind: str
    detail: str


def validate_code_ast(code: str) -> list[AstViolation]:
    """Walk the AST and collect all whitelist violations.

    Returns an empty list if the code is acceptable. Raises ``SyntaxError``
    only for parser-level failures (caller should translate that to a
    ``ToolDefinitionError`` separately).
    """
    tree = ast.parse(code)
    violations: list[AstViolation] = []
    for node in ast.walk(tree):
        cls = type(node)
        line = getattr(node, "lineno", 0)
        col = getattr(node, "col_offset", 0)

        if cls in DENIED_NODES:
            violations.append(AstViolation(line, col, "denied_node", cls.__name__))
            continue
        if cls not in ALLOWED_NODES:
            violations.append(AstViolation(line, col, "unknown_node", cls.__name__))

        if isinstance(node, ast.Attribute):
            if any(node.attr.startswith(p) for p in DENIED_ATTR_PREFIXES):
                violations.append(AstViolation(line, col, "denied_attr", node.attr))

        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in DENIED_CALL_NAMES:
                violations.append(AstViolation(line, col, "denied_call", fn.id))

    return violations


# ---------------------------------------------------------------------------
# Sandbox namespace
# ---------------------------------------------------------------------------

SAFE_BUILTINS: dict[str, Any] = {
    # Numeric / sequence builders
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
    "range": range, "enumerate": enumerate, "zip": zip,
    "map": map, "filter": filter, "sorted": sorted, "reversed": reversed,
    "round": round, "any": any, "all": all,
    # Type constructors
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "dict": dict, "set": set, "tuple": tuple, "frozenset": frozenset,
    "bytes": bytes, "bytearray": bytearray,
    # Type checks
    "isinstance": isinstance, "issubclass": issubclass,
    # Constants
    "True": True, "False": False, "None": None,
    # Print is replaced with no-op so user code can leave debug prints in
    "print": lambda *a, **k: None,
    # Exceptions allowed
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError, "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
}

INJECTED_MODULES: dict[str, Any] = {
    "math": math,
    "statistics": statistics,
    "json": json,
    "re": re,
    "datetime": _dt,
    "random": random,
}


def build_sandbox_globals(extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Construct the globals dict used by ``exec``.

    ``extra`` is reserved for *system-owned* tools (e.g. seed handlers that need
    a shared in-memory store). User-registered code never receives ``extra``.
    """
    g: dict[str, Any] = {
        "__builtins__": dict(SAFE_BUILTINS),
        **INJECTED_MODULES,
    }
    if extra:
        g.update(extra)
    return g


# ---------------------------------------------------------------------------
# Compile + signature check
# ---------------------------------------------------------------------------

HandlerCallable = Callable[..., Any]


def compile_handler(code: str, parameters: dict,
                    extra_globals: Optional[dict[str, Any]] = None) -> HandlerCallable:
    """Validate AST, exec into a sandbox namespace, return ``handler``.

    The handler is a closure over ``extra_globals`` if provided. Raises
    :class:`ToolDefinitionError` on any failure.
    """
    # 1. AST whitelist
    try:
        violations = validate_code_ast(code)
    except SyntaxError as e:
        raise ToolDefinitionError(
            "syntax_error", str(e), line=e.lineno, col=e.offset
        ) from e
    if violations:
        v = violations[0]
        raise ToolDefinitionError(
            f"ast_{v.kind}", v.detail, line=v.line, col=v.col
        )

    # 2. exec into isolated namespace
    sb_globals = build_sandbox_globals(extra=extra_globals)
    sb_locals: dict[str, Any] = {}
    try:
        compiled = compile(code, filename="<toollab>", mode="exec")
        exec(compiled, sb_globals, sb_locals)  # noqa: S102 — sandboxed
    except Exception as e:
        raise ToolDefinitionError("compile_error", str(e)) from e

    # 3. handler symbol present
    handler = sb_locals.get("handler")
    if not callable(handler):
        raise ToolDefinitionError("handler_not_defined",
                                  "module must define `handler`")

    # 4. signature ↔ parameters match (names + required-ness)
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError) as e:
        raise ToolDefinitionError("handler_inspect_failed", str(e)) from e

    expected_keys = set((parameters.get("properties") or {}).keys())
    expected_required = set(parameters.get("required") or [])
    actual_keys = set(sig.parameters.keys())
    actual_required = {
        n for n, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                           inspect.Parameter.VAR_KEYWORD)
    }

    missing = expected_keys - actual_keys
    extra = actual_keys - expected_keys
    # Allow handler to accept *args/**kwargs without flagging.
    extra_strict = {
        n for n in extra
        if sig.parameters[n].kind not in (inspect.Parameter.VAR_POSITIONAL,
                                          inspect.Parameter.VAR_KEYWORD)
    }
    if missing or extra_strict:
        raise ToolDefinitionError(
            "args_mismatch",
            f"missing={sorted(missing)} extra={sorted(extra_strict)}",
        )
    if actual_required - expected_required:
        # Handler requires arg that the schema marks optional.
        raise ToolDefinitionError(
            "required_mismatch",
            f"handler_required={sorted(actual_required)} "
            f"schema_required={sorted(expected_required)}",
        )

    return handler


# ---------------------------------------------------------------------------
# Timeout-bounded execution
# ---------------------------------------------------------------------------

# A small shared pool — handlers are short-lived and one in-flight per call.
# Size is configurable via the API layer when constructing the runner.
_default_pool: Optional[ThreadPoolExecutor] = None


def _get_pool(size: int = 4) -> ThreadPoolExecutor:
    global _default_pool
    if _default_pool is None:
        _default_pool = ThreadPoolExecutor(
            max_workers=size, thread_name_prefix="toollab-handler"
        )
    return _default_pool


def run_handler_with_timeout(
    handler: HandlerCallable,
    kwargs: dict,
    timeout_ms: int,
    pool: Optional[ThreadPoolExecutor] = None,
) -> dict:
    """Invoke ``handler(**kwargs)`` with a wall-clock timeout.

    Returns a structured dict (not the raw handler return) so callers always
    have a uniform shape::

        {
          "ok": True,  "result": <handler return>, "latency_ms": int
        } | {
          "ok": False, "error_kind": "timeout"|"exception",
          "error": str, "latency_ms": int
        }
    """
    pool = pool or _get_pool()
    started = time.monotonic()
    fut = pool.submit(handler, **kwargs)
    try:
        result = fut.result(timeout=timeout_ms / 1000)
    except FutTimeoutError:
        return {
            "ok": False,
            "error_kind": "timeout",
            "error": f"handler exceeded {timeout_ms} ms",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as e:  # noqa: BLE001 — handler is user code
        return {
            "ok": False,
            "error_kind": "exception",
            "error": f"{type(e).__name__}: {e}",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    return {
        "ok": True,
        "result": result,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


# ---------------------------------------------------------------------------
# Return-schema validation
# ---------------------------------------------------------------------------


def validate_return(value: Any, returns_schema: dict) -> Optional[dict]:
    """Validate handler return against its declared schema.

    Returns ``None`` on success, or an error payload dict (which the caller
    forwards to the LLM as the tool message content).
    """
    try:
        jsonschema.validate(value, returns_schema)
        return None
    except jsonschema.ValidationError as e:
        return {
            "error_kind": "return_schema_invalid",
            "path": [str(p) for p in e.absolute_path],
            "message": e.message,
        }
    except jsonschema.SchemaError as e:
        return {
            "error_kind": "return_schema_broken",
            "message": e.message,
        }


def validate_jsonschema(schema: dict) -> Optional[str]:
    """Validate a JSON Schema document itself (used at registration).

    Returns ``None`` if the schema is valid, otherwise a human-readable error.
    """
    try:
        jsonschema.validators.Draft202012Validator.check_schema(schema)
        return None
    except jsonschema.SchemaError as e:
        return e.message
