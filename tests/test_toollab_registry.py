"""Tests for ``libs.toollab.registry`` — version bumps, visibility, execute."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from libs.toollab.registry import SYSTEM_OWNER, ToolRegistry


def _td(*, owner="user-1", name="echo", version=1, code=None, params=None,
        returns=None, tool_id=None, is_public=False):
    """Mimic the ToolDefinition ORM row (only the fields the registry reads)."""
    return SimpleNamespace(
        id=tool_id or uuid.uuid4(),
        owner_user_id=owner,
        name=name,
        version=version,
        description=f"echo tool {name}",
        parameters_json=params or {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
        returns_json=returns or {
            "type": "object",
            "properties": {"out": {"type": "integer"}},
            "required": ["out"],
        },
        code=code or "def handler(x):\n    return {'out': x}\n",
        is_public=is_public,
    )


class TestRegisterAndVersion:
    def test_register_then_lookup(self):
        reg = ToolRegistry()
        td = _td()
        reg.register(td)
        assert reg.get_by_id(td.id) is not None

    def test_replace_active_drops_previous(self):
        reg = ToolRegistry()
        first = _td(version=1)
        reg.register(first)
        second = _td(version=2)  # new id
        reg.replace_active(second, previous_id=first.id)
        assert reg.get_by_id(first.id) is None
        assert reg.get_by_id(second.id) is not None


class TestVisibility:
    def test_user_sees_own_and_system_only(self):
        reg = ToolRegistry()
        own = _td(owner="user-1", name="alpha")
        system = _td(owner=SYSTEM_OWNER, name="beta")
        other = _td(owner="user-2", name="gamma")
        reg.register(own)
        reg.register(system)
        reg.register(other)

        names = sorted(t.name for t in reg.list_for_user("user-1"))
        assert names == ["alpha", "beta"]

    def test_filter_by_tool_ids(self):
        reg = ToolRegistry()
        a = _td(owner="user-1", name="alpha")
        b = _td(owner="user-1", name="beta")
        reg.register(a)
        reg.register(b)
        out = reg.list_for_user("user-1", tool_ids=[a.id])
        assert [t.name for t in out] == ["alpha"]

    def test_public_tool_from_other_user_is_visible(self):
        reg = ToolRegistry()
        reg.register(_td(owner="user-1", name="own"))
        reg.register(_td(owner="user-2", name="shared", is_public=True))
        reg.register(_td(owner="user-2", name="secret", is_public=False))
        names = sorted(t.name for t in reg.list_for_user("user-1"))
        assert names == ["own", "shared"]

    def test_public_tool_executable_by_other_user(self):
        reg = ToolRegistry()
        reg.register(_td(owner="user-2", name="shared", is_public=True))
        result = reg.execute("shared", {"x": 7}, user_sub="user-1")
        assert result["ok"] is True
        assert result["result"] == {"out": 7}

    def test_private_tool_not_executable_by_other_user(self):
        reg = ToolRegistry()
        reg.register(_td(owner="user-2", name="secret", is_public=False))
        result = reg.execute("secret", {"x": 1}, user_sub="user-1")
        assert result["ok"] is False
        assert result["error_kind"] == "tool_not_found"


class TestExecute:
    def test_execute_unknown_tool_returns_error(self):
        reg = ToolRegistry()
        result = reg.execute("nope", {}, user_sub="user-1")
        assert result["ok"] is False
        assert result["error_kind"] == "tool_not_found"

    def test_execute_calls_handler(self):
        reg = ToolRegistry()
        reg.register(_td(owner="user-1", name="echo"))
        result = reg.execute("echo", {"x": 42}, user_sub="user-1")
        assert result["ok"] is True
        assert result["result"] == {"out": 42}
        assert result["name"] == "echo"

    def test_execute_validates_return_schema(self):
        reg = ToolRegistry()
        # handler returns wrong shape
        bad = _td(
            owner="user-1", name="bad",
            code="def handler(x):\n    return {'wrong': x}\n",
        )
        reg.register(bad)
        result = reg.execute("bad", {"x": 1}, user_sub="user-1")
        assert result["ok"] is False
        assert result["error_kind"] == "return_schema_invalid"

    def test_execute_owner_isolation(self):
        reg = ToolRegistry()
        # Tool only visible to user-1, but user-2 tries to invoke.
        reg.register(_td(owner="user-1", name="secret"))
        result = reg.execute("secret", {"x": 1}, user_sub="user-2")
        assert result["ok"] is False
        assert result["error_kind"] == "tool_not_found"
