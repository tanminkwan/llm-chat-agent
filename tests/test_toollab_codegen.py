"""``libs.toollab.codegen`` — AST 기반 JSON Schema 생성기 단위 테스트."""
from __future__ import annotations

import pytest

from libs.toollab.codegen import (
    SchemaGenerationError,
    generate_schemas_from_code,
)


class TestBasicTypes:
    def test_int_str_bool(self):
        code = (
            "def handler(x: int, name: str, flag: bool) -> dict:\n"
            "    return {'ok': True}\n"
        )
        gs = generate_schemas_from_code(code)
        props = gs.parameters["properties"]
        assert props["x"] == {"type": "integer"}
        assert props["name"] == {"type": "string"}
        assert props["flag"] == {"type": "boolean"}
        assert set(gs.parameters["required"]) == {"x", "name", "flag"}
        assert gs.parameters["additionalProperties"] is False

    def test_default_makes_param_optional(self):
        code = (
            "def handler(x: int, y: int = 10) -> dict:\n"
            "    return {'sum': x + y}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["required"] == ["x"]
        assert "y" in gs.parameters["properties"]

    def test_keyword_only_args(self):
        code = (
            "def handler(*, x: int, y: int = 1) -> dict:\n"
            "    return {'r': x}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["required"] == ["x"]
        assert set(gs.parameters["properties"].keys()) == {"x", "y"}


class TestGenerics:
    def test_list_of_int(self):
        code = (
            "def handler(xs: list[int]) -> dict:\n"
            "    return {'sum': sum(xs)}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["properties"]["xs"] == {
            "type": "array",
            "items": {"type": "integer"},
        }

    def test_dict_of_str_int(self):
        code = (
            "def handler(m: dict[str, int]) -> dict:\n"
            "    return {'k': list(m.keys())}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["properties"]["m"] == {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }

    def test_optional_via_typing(self):
        code = (
            "def handler(x: Optional[int]) -> dict:\n"
            "    return {'x': x}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["properties"]["x"] == {"type": ["integer", "null"]}

    def test_pep604_union_with_none(self):
        code = (
            "def handler(x: int | None) -> dict:\n"
            "    return {'x': x}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["properties"]["x"] == {"type": ["integer", "null"]}

    def test_union_multiple_types(self):
        code = (
            "def handler(x: int | str) -> dict:\n"
            "    return {'x': x}\n"
        )
        gs = generate_schemas_from_code(code)
        schema = gs.parameters["properties"]["x"]
        # 둘 다 단순 타입이라 anyOf 로 떨어진다.
        assert "anyOf" in schema
        assert {"type": "integer"} in schema["anyOf"]
        assert {"type": "string"} in schema["anyOf"]


class TestReturnSchemas:
    def test_bare_dict_inferred_from_param_ref(self):
        """`-> dict` + 본문에서 param 을 그대로 반환 → 파라미터 타입이 그대로 박힘."""
        code = (
            "def handler(x: int) -> dict:\n"
            "    return {'out': x}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.returns["type"] == "object"
        assert gs.returns["properties"]["out"] == {"type": "integer"}
        assert gs.returns["required"] == ["out"]
        assert gs.returns["additionalProperties"] is False

    def test_dict_return_mixes_param_and_local_var(self):
        """파라미터/로컬 변수/재할당이 섞인 사용자 시나리오 (실제 보고된 케이스)."""
        code = (
            "def handler(x: int, y: str) -> dict:\n"
            "    m = 'Hello'\n"
            "    y = 3.14\n"
            "    return {'out': x, 'm': m, 'y': y}\n"
        )
        gs = generate_schemas_from_code(code)
        props = gs.returns["properties"]
        assert props["out"] == {"type": "integer"}
        assert props["m"] == {"type": "string"}
        # y 는 파라미터 hint(str) 가 본문에서 float 로 재할당됨 — 마지막 할당이 이김.
        assert props["y"] == {"type": "number"}
        assert set(gs.returns["required"]) == {"out", "m", "y"}

    def test_dict_return_with_literal_values(self):
        code = (
            "def handler(x: int) -> dict:\n"
            "    return {'flag': True, 'name': 'a', 'cnt': 0}\n"
        )
        gs = generate_schemas_from_code(code)
        props = gs.returns["properties"]
        assert props["flag"] == {"type": "boolean"}
        assert props["name"] == {"type": "string"}
        assert props["cnt"] == {"type": "integer"}

    def test_dict_return_unknown_value_falls_back_to_string(self):
        """추정 못 한 값은 type 하나만 고치면 되도록 string 으로 떨어진다."""
        code = (
            "def handler(x: int) -> dict:\n"
            "    return {'a': x, 'b': some_unknown_func()}\n"
        )
        gs = generate_schemas_from_code(code)
        props = gs.returns["properties"]
        assert props["a"] == {"type": "integer"}
        assert props["b"] == {"type": "string"}  # 사용자가 type 만 바꿔 쓰면 됨

    def test_typed_dict_kv_kept_as_object(self):
        """`dict[str, V]` 처럼 내부 타입을 알면 그대로 유지 (보강 안 함)."""
        code = (
            "def handler(x: int) -> dict[str, int]:\n"
            "    return {'out': x}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.returns["type"] == "object"
        assert gs.returns.get("additionalProperties") == {"type": "integer"}

    def test_unknown_class_with_dict_literal_still_inferred(self):
        """반환 어노테이션이 사용자 정의 클래스여도 본문에 dict literal 이 있으면 보강."""
        code = (
            "def handler(x: int) -> MyResult:\n"
            "    return {'out': x}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.returns["type"] == "object"
        assert gs.returns["properties"]["out"] == {"type": "integer"}

    def test_non_dict_return_falls_back_to_bare_object(self):
        """본문이 dict literal 이 아니면 빈 object 로 둔다 (거짓 정보 X)."""
        code = (
            "def handler(x: int) -> dict:\n"
            "    return some_factory(x)\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.returns == {"type": "object"}

    def test_scalar_return_wrapped_in_result(self):
        code = (
            "def handler(x: int) -> int:\n"
            "    return x\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.returns == {
            "type": "object",
            "properties": {"result": {"type": "integer"}},
            "required": ["result"],
        }
        assert any("자동 래핑" in w for w in gs.warnings)

    def test_list_return_wrapped(self):
        code = (
            "def handler(xs: list[int]) -> list[int]:\n"
            "    return [x * 2 for x in xs]\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.returns["type"] == "object"
        assert gs.returns["properties"]["result"] == {
            "type": "array",
            "items": {"type": "integer"},
        }


class TestErrors:
    def test_missing_param_annotation(self):
        code = (
            "def handler(x, y: int) -> dict:\n"
            "    return {'r': x + y}\n"
        )
        with pytest.raises(SchemaGenerationError) as ei:
            generate_schemas_from_code(code)
        assert ei.value.kind == "missing_param_annotation"
        assert "`x`" in ei.value.detail

    def test_missing_return_annotation(self):
        code = (
            "def handler(x: int):\n"
            "    return {'r': x}\n"
        )
        with pytest.raises(SchemaGenerationError) as ei:
            generate_schemas_from_code(code)
        assert ei.value.kind == "missing_return_annotation"

    def test_no_handler_function(self):
        code = "def other(x: int) -> dict:\n    return {'r': x}\n"
        with pytest.raises(SchemaGenerationError) as ei:
            generate_schemas_from_code(code)
        assert ei.value.kind == "handler_not_defined"

    def test_syntax_error(self):
        with pytest.raises(SchemaGenerationError) as ei:
            generate_schemas_from_code("def handler(x: int) -> dict\n    return {}\n")
        assert ei.value.kind == "syntax_error"
        assert ei.value.line is not None


class TestWarnings:
    def test_unknown_type_warns_not_errors(self):
        code = (
            "def handler(x: SomeCustomType) -> dict:\n"
            "    return {'r': 1}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["properties"]["x"] == {}
        assert any("SomeCustomType" in w for w in gs.warnings)

    def test_var_args_warns(self):
        code = (
            "def handler(x: int, *args, **kwargs) -> dict:\n"
            "    return {'r': x}\n"
        )
        gs = generate_schemas_from_code(code)
        assert "x" in gs.parameters["properties"]
        # *args / **kwargs 는 스키마에 들어가지 않고 경고만 추가됨.
        assert "args" not in gs.parameters["properties"]
        assert "kwargs" not in gs.parameters["properties"]
        assert any("*args" in w for w in gs.warnings)
        assert any("**kwargs" in w for w in gs.warnings)


class TestLiteralAndEnum:
    def test_literal_becomes_enum(self):
        code = (
            "def handler(mode: Literal['a', 'b']) -> dict:\n"
            "    return {'mode': mode}\n"
        )
        gs = generate_schemas_from_code(code)
        assert gs.parameters["properties"]["mode"] == {"enum": ["a", "b"]}
