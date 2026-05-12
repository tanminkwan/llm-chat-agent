"""AST 기반 JSON Schema 생성기 (Tool Lab — "Generate from code").

Tool Lab UI 의 ``code`` 텍스트박스에 입력된 ``def handler(...)`` 함수의
**타입 힌트** 만 보고 ``parameters`` / ``returns`` JSON Schema 초안을 만들어
준다. 사용자가 결과를 검토한 뒤 저장 버튼을 누르므로, 여기서는 결정적인
변환만 수행하고 LLM 호출은 하지 않는다.

규칙(=UI 가이드 문구):

* 모든 파라미터에 타입 힌트 필수 — 빠진 파라미터가 하나라도 있으면
  :class:`SchemaGenerationError` ``missing_param_annotation`` 발생.
* 반환 타입 힌트 필수 (`-> ...`) — 빠지면 ``missing_return_annotation``.
* 반환 타입은 ``dict[str, ...]`` / ``TypedDict`` 가 아니어도 받지만 LLM 호환을
  위해 dict 키 구조를 표현할 수 있도록 ``dict`` 형태를 추천. 단일 스칼라
  반환이면 ``{"type": "object", "properties": {"result": <T>}}`` 로 래핑한다.
* 기본값이 있는 파라미터는 ``required`` 에서 제외된다.
* 지원 타입: ``int``, ``float``, ``str``, ``bool``, ``list``/``list[T]``,
  ``dict``/``dict[str, T]``, ``Optional[T]`` (= ``T | None``), ``Any``.
  ``TypedDict`` 와 사용자 정의 클래스는 ``object`` 로만 표기되고
  ``warnings`` 에 메모를 추가한다.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


class SchemaGenerationError(Exception):
    """타입 힌트 누락 등 사용자에게 보여줄 수 있는 변환 실패."""

    def __init__(self, kind: str, detail: str, line: int | None = None,
                 col: int | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.line = line
        self.col = col


@dataclass
class GeneratedSchemas:
    parameters: dict
    returns: dict
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Type annotation → JSON Schema fragment
# ---------------------------------------------------------------------------


_PRIMITIVES: dict[str, dict] = {
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "str": {"type": "string"},
    "bool": {"type": "boolean"},
    "bytes": {"type": "string", "contentEncoding": "base64"},
    "None": {"type": "null"},
    "NoneType": {"type": "null"},
    "Any": {},
    "object": {},
}


def _is_none_literal(node: ast.AST) -> bool:
    return (
        (isinstance(node, ast.Constant) and node.value is None)
        or (isinstance(node, ast.Name) and node.id in ("None", "NoneType"))
    )


def _ann_to_schema(node: ast.AST | None, warnings: list[str]) -> dict:
    """단일 annotation AST 노드를 JSON Schema fragment 로 변환."""
    if node is None:
        return {}

    # bare name: int, str, list, dict, Any, ...
    if isinstance(node, ast.Name):
        name = node.id
        if name in _PRIMITIVES:
            return dict(_PRIMITIVES[name])
        if name == "list":
            return {"type": "array"}
        if name == "dict":
            return {"type": "object"}
        if name == "tuple":
            return {"type": "array"}
        warnings.append(
            f"unknown type `{name}` — JSON Schema 에서는 빈 object 로 표기됨"
        )
        return {}

    # qualified: typing.List, typing.Optional, ...
    if isinstance(node, ast.Attribute):
        return _ann_to_schema(ast.Name(id=node.attr), warnings)

    # Constant: forward-ref 'int', None
    if isinstance(node, ast.Constant):
        if node.value is None:
            return {"type": "null"}
        if isinstance(node.value, str):
            # forward-reference 문자열 — 다시 파싱해 본다.
            try:
                inner = ast.parse(node.value, mode="eval").body
                return _ann_to_schema(inner, warnings)
            except SyntaxError:
                warnings.append(
                    f"forward-ref `{node.value}` 를 해석하지 못했습니다"
                )
                return {}
        return {}

    # Subscript: list[int], dict[str, int], Optional[int], Union[...], tuple[...]
    if isinstance(node, ast.Subscript):
        outer = node.value
        slice_node = node.slice
        # Python <3.9 의 ast.Index 호환
        if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):  # type: ignore[attr-defined]
            slice_node = slice_node.value  # type: ignore[attr-defined]

        outer_name = _name_of(outer)
        if outer_name in ("Optional",):
            inner = _ann_to_schema(slice_node, warnings)
            return _union_with_null(inner)
        if outer_name in ("Union",):
            members = (
                [_ann_to_schema(elt, warnings) for elt in slice_node.elts]
                if isinstance(slice_node, ast.Tuple)
                else [_ann_to_schema(slice_node, warnings)]
            )
            return _union(members)
        if outer_name in ("Literal",):
            values = (
                [v.value for v in slice_node.elts if isinstance(v, ast.Constant)]
                if isinstance(slice_node, ast.Tuple)
                else ([slice_node.value] if isinstance(slice_node, ast.Constant) else [])
            )
            if values:
                return {"enum": values}
            return {}
        if outer_name in ("list", "List", "Sequence", "Iterable"):
            return {"type": "array", "items": _ann_to_schema(slice_node, warnings)}
        if outer_name in ("tuple", "Tuple"):
            if isinstance(slice_node, ast.Tuple) and slice_node.elts:
                items = [_ann_to_schema(e, warnings) for e in slice_node.elts
                         if not (isinstance(e, ast.Constant) and e.value is Ellipsis)]
                return {"type": "array", "items": items, "minItems": len(items),
                        "maxItems": len(items)}
            return {"type": "array", "items": _ann_to_schema(slice_node, warnings)}
        if outer_name in ("dict", "Dict", "Mapping"):
            # dict[K, V] 에서 V 만 사용 (JSON 의 키는 항상 문자열).
            if isinstance(slice_node, ast.Tuple) and len(slice_node.elts) == 2:
                v = _ann_to_schema(slice_node.elts[1], warnings)
                return {"type": "object", "additionalProperties": v} if v else {"type": "object"}
            return {"type": "object"}
        if outer_name in ("set", "Set", "frozenset", "FrozenSet"):
            return {"type": "array", "items": _ann_to_schema(slice_node, warnings),
                    "uniqueItems": True}
        warnings.append(
            f"unknown generic `{outer_name}[...]` — 빈 object 로 표기됨"
        )
        return {}

    # X | Y | None  (PEP 604)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        members = _flatten_union(node, warnings)
        return _union(members)

    warnings.append(f"unsupported annotation `{ast.dump(node)}`")
    return {}


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _flatten_union(node: ast.AST, warnings: list[str]) -> list[dict]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flatten_union(node.left, warnings) + _flatten_union(node.right, warnings)
    return [_ann_to_schema(node, warnings)]


def _union(members: list[dict]) -> dict:
    cleaned = [m for m in members if m]
    if not cleaned:
        return {}
    if len(cleaned) == 1:
        return cleaned[0]
    # Optional[T] (= T | None) 의 흔한 경우 — type 배열로 합칠 수 있으면 합친다.
    null_members = [m for m in cleaned if m == {"type": "null"}]
    others = [m for m in cleaned if m != {"type": "null"}]
    if null_members and len(others) == 1 and list(others[0].keys()) == ["type"] \
            and isinstance(others[0]["type"], str):
        return {"type": [others[0]["type"], "null"]}
    return {"anyOf": cleaned}


def _union_with_null(inner: dict) -> dict:
    if not inner:
        return {"type": ["null"]}  # rare; degenerate
    if list(inner.keys()) == ["type"] and isinstance(inner["type"], str):
        return {"type": [inner["type"], "null"]}
    return {"anyOf": [inner, {"type": "null"}]}


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Return-statement inference (`-> dict` 의 내부 키 추정)
# ---------------------------------------------------------------------------
#
# 핵심 아이디어: ``handler`` 의 마지막 ``return {...}`` literal 을 들여다보고,
# 각 key 에 들어가는 값 식의 타입을 추정해 정확한 ``properties`` 를 만든다.
# 값이 변수 참조면 함수 본문을 source order 로 훑어 만든 "type env" 에서 찾고,
# 식 자체가 상수/리스트/dict literal/잘 알려진 type constructor 호출이면 직접
# 추론한다. 추론에 실패한 필드는 ``{"type": "string"}`` 으로 떨어뜨려 사용자가
# **type 한 글자만 고치면** 되도록 한다.


def _const_schema(value: Any) -> dict:
    # bool 은 isinstance(value, int) 보다 먼저 체크 (Python 의 bool 은 int 의 서브타입).
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if value is None:
        return {"type": "null"}
    return {}


_CALL_RETURN_TYPES: dict[str, dict] = {
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "str": {"type": "string"},
    "bool": {"type": "boolean"},
    "list": {"type": "array"},
    "dict": {"type": "object"},
    "set": {"type": "array"},
    "tuple": {"type": "array"},
    "len": {"type": "integer"},
    "abs": {"type": "number"},
    "round": {"type": "number"},
    "min": {"type": "number"},
    "max": {"type": "number"},
    "sum": {"type": "number"},
}


def _infer_expr_schema(node: ast.AST, env: dict[str, dict]) -> dict:
    """임의의 식 노드의 JSON Schema fragment 를 베스트-에포트로 추정."""
    if isinstance(node, ast.Constant):
        return _const_schema(node.value)
    if isinstance(node, ast.Name):
        if node.id == "True" or node.id == "False":
            return {"type": "boolean"}
        if node.id == "None":
            return {"type": "null"}
        return dict(env.get(node.id, {}))
    if isinstance(node, ast.JoinedStr):  # f-string
        return {"type": "string"}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        if isinstance(node, ast.List) and node.elts:
            inner = _infer_expr_schema(node.elts[0], env)
            return {"type": "array", "items": inner} if inner else {"type": "array"}
        return {"type": "array"}
    if isinstance(node, ast.Dict):
        nested = _dict_literal_to_schema(node, env)
        return nested if nested is not None else {"type": "object"}
    if isinstance(node, ast.BoolOp) or isinstance(node, ast.Compare):
        return {"type": "boolean"}
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return {"type": "boolean"}
        return _infer_expr_schema(node.operand, env)
    if isinstance(node, ast.BinOp):
        left = _infer_expr_schema(node.left, env)
        right = _infer_expr_schema(node.right, env)
        if left.get("type") == "string" or right.get("type") == "string":
            return {"type": "string"}
        if left.get("type") == "integer" and right.get("type") == "integer" \
                and not isinstance(node.op, ast.Div):
            return {"type": "integer"}
        if {left.get("type"), right.get("type")} <= {"integer", "number"}:
            return {"type": "number"}
        return {}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return dict(_CALL_RETURN_TYPES.get(node.func.id, {}))
    if isinstance(node, ast.IfExp):
        a = _infer_expr_schema(node.body, env)
        b = _infer_expr_schema(node.orelse, env)
        return a if a and a == b else {}
    return {}


def _dict_literal_to_schema(dict_node: ast.Dict, env: dict[str, dict]) -> dict | None:
    """``ast.Dict`` literal → object schema. 모든 key 가 문자열 상수일 때만 성공."""
    properties: dict[str, dict] = {}
    required: list[str] = []
    for k, v in zip(dict_node.keys, dict_node.values):
        if k is None:
            return None  # **expansion — 구조를 알 수 없음
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            return None
        inferred = _infer_expr_schema(v, env)
        # 추정 실패 시 fallback: 사용자가 type 만 살짝 바꿔 쓰도록 string 로 둠.
        properties[k.value] = inferred if inferred else {"type": "string"}
        required.append(k.value)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _build_local_env(handler: ast.FunctionDef) -> dict[str, dict]:
    """파라미터 힌트 + 본문 내 할당으로 ``var name → schema`` 환경 구축.

    같은 변수에 여러 번 할당될 수 있으므로 source order (line, col) 로 정렬해
    가장 마지막 할당이 환경에 남게 한다. 파라미터 타입 힌트가 있어도 본문
    어느 줄에서 다시 할당하면 그 타입으로 덮어쓴다 — Python 의 런타임 의미를
    그대로 반영.
    """
    env: dict[str, dict] = {}
    for arg in (list(handler.args.posonlyargs)
                + list(handler.args.args)
                + list(handler.args.kwonlyargs)):
        if arg.annotation is not None:
            schema = _ann_to_schema(arg.annotation, [])
            if schema:
                env[arg.arg] = schema

    assigns: list[ast.AST] = []
    for stmt in ast.walk(handler):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            assigns.append(stmt)
    assigns.sort(key=lambda s: (getattr(s, "lineno", 0), getattr(s, "col_offset", 0)))

    for stmt in assigns:
        if isinstance(stmt, ast.Assign):
            schema = _infer_expr_schema(stmt.value, env)
            if not schema:
                continue
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    env[tgt.id] = schema
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            schema = _ann_to_schema(stmt.annotation, [])
            if schema:
                env[stmt.target.id] = schema
    return env


def _find_last_return(handler: ast.FunctionDef) -> ast.Return | None:
    """소스에서 가장 아래쪽에 위치한 ``return`` 노드를 찾는다."""
    latest: ast.Return | None = None
    for node in ast.walk(handler):
        if isinstance(node, ast.Return):
            if latest is None or (getattr(node, "lineno", 0)
                                  > getattr(latest, "lineno", 0)):
                latest = node
    return latest


def _infer_dict_return_schema(
    handler: ast.FunctionDef, warnings: list[str],
) -> dict | None:
    """``-> dict`` 등 모호한 어노테이션을 본문의 ``return {...}`` literal 로 보강."""
    last_return = _find_last_return(handler)
    if last_return is None or last_return.value is None:
        return None
    if not isinstance(last_return.value, ast.Dict):
        return None
    env = _build_local_env(handler)
    schema = _dict_literal_to_schema(last_return.value, env)
    if schema is None:
        warnings.append(
            "반환 dict 에 동적 key 또는 `**spread` 가 있어 자동 추정을 포기했습니다."
        )
    return schema


def generate_schemas_from_code(code: str) -> GeneratedSchemas:
    """``code`` 안의 ``handler`` 함수에서 parameters/returns JSON Schema 를 추출.

    실패 시 :class:`SchemaGenerationError`. UI 는 ``kind`` / ``detail`` 을
    그대로 사용자에게 노출하므로 메시지는 사람이 읽을 수 있게 작성한다.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SchemaGenerationError(
            "syntax_error",
            f"코드 파싱 실패: {e.msg}",
            line=e.lineno,
            col=e.offset,
        ) from e

    handler: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "handler":
            handler = node
            break
    if handler is None:
        raise SchemaGenerationError(
            "handler_not_defined",
            "`def handler(...)` 함수를 찾을 수 없습니다.",
        )

    warnings: list[str] = []
    properties: dict[str, dict] = {}
    required: list[str] = []

    args = handler.args
    # positional + positional-or-keyword + keyword-only 를 한 줄로 펼쳐서 검사.
    positional = list(args.posonlyargs) + list(args.args)
    pos_defaults = list(args.defaults)
    # defaults 는 뒤쪽 positional 부터 매칭됨.
    pad = [None] * (len(positional) - len(pos_defaults))
    positional_defaults: list[ast.AST | None] = pad + pos_defaults  # type: ignore[assignment]
    kwonly = list(args.kwonlyargs)
    kw_defaults = list(args.kw_defaults)  # 길이 = len(kwonlyargs), None = required

    for arg, default in zip(positional, positional_defaults):
        if arg.annotation is None:
            raise SchemaGenerationError(
                "missing_param_annotation",
                f"파라미터 `{arg.arg}` 에 타입 힌트가 없습니다. "
                f"예: `{arg.arg}: int`",
                line=arg.lineno,
                col=arg.col_offset,
            )
        properties[arg.arg] = _ann_to_schema(arg.annotation, warnings)
        if default is None:
            required.append(arg.arg)

    for arg, default in zip(kwonly, kw_defaults):
        if arg.annotation is None:
            raise SchemaGenerationError(
                "missing_param_annotation",
                f"파라미터 `{arg.arg}` 에 타입 힌트가 없습니다.",
                line=arg.lineno,
                col=arg.col_offset,
            )
        properties[arg.arg] = _ann_to_schema(arg.annotation, warnings)
        if default is None:
            required.append(arg.arg)

    if args.vararg is not None:
        warnings.append(
            f"`*{args.vararg.arg}` 는 JSON Schema 로 표현할 수 없어 스키마에서 생략됩니다."
        )
    if args.kwarg is not None:
        warnings.append(
            f"`**{args.kwarg.arg}` 는 JSON Schema 로 표현할 수 없어 스키마에서 생략됩니다."
        )

    parameters: dict = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required

    if handler.returns is None:
        raise SchemaGenerationError(
            "missing_return_annotation",
            "`handler` 함수에 반환 타입 힌트(`-> ...`)가 없습니다. "
            "예: `def handler(x: int) -> dict:`",
            line=handler.lineno,
            col=handler.col_offset,
        )

    returns_schema = _ann_to_schema(handler.returns, warnings)

    # 어노테이션만으로 내부 구조를 알 수 없는 경우 (`-> dict` / `-> Any` / 사용자
    # 정의 클래스 / 등) 본문의 `return {...}` literal 을 보고 properties 를 만든다.
    is_ambiguous_object = (
        not returns_schema
        or (returns_schema.get("type") == "object"
            and "properties" not in returns_schema
            and "additionalProperties" not in returns_schema)
    )
    if is_ambiguous_object:
        inferred = _infer_dict_return_schema(handler, warnings)
        if inferred is not None:
            returns_schema = inferred

    returns = _normalise_return(returns_schema, warnings)

    return GeneratedSchemas(
        parameters=parameters,
        returns=returns,
        warnings=warnings,
    )


def _normalise_return(schema: dict, warnings: list[str]) -> dict:
    """반환 스키마를 LLM tool calling 에 적합한 형태로 정리.

    * 이미 object 면 그대로 둔다 (key 별 추정은 :func:`_infer_dict_return_schema`
      가 미리 끝낸 상태).
    * 본문에서 dict literal 을 못 찾아 정말 모호하게 남은 경우는 빈
      ``{"type": "object"}`` 로 둔다 — 사용자에게 거짓 정보를 주지 않기 위해
      properties 는 비워둔다.
    * 스칼라/배열 등 object 가 아닌 반환은 ``{"result": <T>}`` 로 래핑한다.
    """
    if not schema:
        return {"type": "object"}
    if schema.get("type") == "object":
        return schema
    if isinstance(schema.get("type"), list) and "object" in schema["type"]:
        return schema
    warnings.append(
        "반환 타입이 dict/object 가 아니라 `{result: <T>}` 형태로 자동 래핑되었습니다."
    )
    return {
        "type": "object",
        "properties": {"result": schema},
        "required": ["result"],
    }
