"""In-memory ``ToolRegistry`` — keeps compiled handlers + LLM-facing tool dicts.

The DB is the source of truth for tool *definitions*; this registry caches the
*compiled* form (sandbox-checked Python ``handler`` callable + the OpenAI-shaped
tool dict that we hand to ``bind_tools``). Recompiling on every request would
be wasteful (~10ms per tool).

Threading model
---------------
The registry is a process-singleton. All write paths (register / replace /
deactivate / unregister) take ``_lock`` so a half-updated state is never
visible to readers. Reads are racy by design — they grab a snapshot reference
to a tuple and never iterate the live dict — so adding a new tool while a
``run`` is mid-flight cannot break the run.

Owner / visibility
------------------
``owner_user_id="system"`` is reserved for seed tools (see :mod:`libs.toollab.seed`).
System-owned tools are visible to every user; user-owned tools are visible
only to their owner (Admin sees everyone's, but the LLM-facing tool catalogue
is still scoped to the *requesting* user — admin status only relaxes
read-list permissions).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from libs.core.settings import settings
from libs.toollab import sandbox
from libs.toollab.models import ToolDefinition

logger = logging.getLogger("llm-chat-agent.toollab.registry")

SYSTEM_OWNER = "system"


@dataclass
class CompiledTool:
    """In-memory cached form of a registered tool."""

    tool_id: UUID
    owner_user_id: str
    name: str
    version: int
    handler: Any
    parameters: dict
    returns: dict
    description: str
    tool_dict: dict  # OpenAI tool format for bind_tools
    is_public: bool = False

    def visible_to(self, user_sub: str) -> bool:
        if self.owner_user_id == SYSTEM_OWNER or self.owner_user_id == user_sub:
            return True
        return self.is_public


class ToolRegistry:
    """Process-singleton registry. Use :func:`get_registry` to access."""

    def __init__(self) -> None:
        # tool_id (UUID) -> CompiledTool
        self._by_id: dict[UUID, CompiledTool] = {}
        # (owner_user_id, name) -> tool_id  (only the *active* version)
        self._by_owner_name: dict[tuple[str, str], UUID] = {}
        self._lock = threading.Lock()
        # System-tool extra globals (e.g. seed tools share an in-memory store).
        self._system_extras: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set_system_extras(self, extras: dict[str, Any]) -> None:
        """Inject system-owner-only globals (seed _USERS dict, etc.).

        Called once at startup before seed registration.
        """
        with self._lock:
            self._system_extras = dict(extras)

    def register(self, td: ToolDefinition) -> CompiledTool:
        """Compile + cache a single tool definition.

        Idempotent for the same ``td.id``: re-registering replaces the entry.
        Marking a different ``(owner, name)`` row as the active version is the
        caller's responsibility (see :meth:`replace_active`).
        """
        is_system = td.owner_user_id == SYSTEM_OWNER
        extras = self._system_extras if is_system else None
        handler = sandbox.compile_handler(
            td.code, td.parameters_json, extra_globals=extras
        )
        compiled = CompiledTool(
            tool_id=td.id,
            owner_user_id=td.owner_user_id,
            name=td.name,
            version=td.version,
            handler=handler,
            parameters=td.parameters_json,
            returns=td.returns_json,
            description=td.description,
            tool_dict={
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.parameters_json,
                },
            },
            is_public=bool(getattr(td, "is_public", False)),
        )
        with self._lock:
            self._by_id[td.id] = compiled
            self._by_owner_name[(td.owner_user_id, td.name)] = td.id
        logger.debug(
            "registry.register tool_id=%s name=%s owner=%s version=%s",
            td.id, td.name, td.owner_user_id, td.version,
        )
        return compiled

    def replace_active(self, td: ToolDefinition,
                       previous_id: Optional[UUID]) -> CompiledTool:
        """Atomically swap the active version: remove old, install new."""
        compiled = self.register(td)
        if previous_id and previous_id != td.id:
            with self._lock:
                self._by_id.pop(previous_id, None)
        return compiled

    def unregister(self, tool_id: UUID) -> None:
        """Drop a tool from the cache (e.g. on deactivate or delete)."""
        with self._lock:
            compiled = self._by_id.pop(tool_id, None)
            if compiled is not None:
                key = (compiled.owner_user_id, compiled.name)
                # Only remove the by-name index if it still points to this id.
                if self._by_owner_name.get(key) == tool_id:
                    self._by_owner_name.pop(key, None)

    def clear(self) -> None:
        """Test-only: drop everything."""
        with self._lock:
            self._by_id.clear()
            self._by_owner_name.clear()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_by_id(self, tool_id: UUID) -> Optional[CompiledTool]:
        return self._by_id.get(tool_id)

    def list_for_user(self, user_sub: str,
                      tool_ids: Optional[list[UUID]] = None) -> list[CompiledTool]:
        """Return tools visible to ``user_sub``, optionally filtered by id list.

        - If ``tool_ids`` is None → all visible tools.
        - If ``tool_ids`` provided → only those that exist *and* are visible.
        - Ordering is name-asc for stable LLM prompts.
        """
        snapshot = list(self._by_id.values())
        if tool_ids is not None:
            wanted = set(tool_ids)
            snapshot = [t for t in snapshot if t.tool_id in wanted]
        snapshot = [t for t in snapshot if t.visible_to(user_sub)]
        # Cap to the configured maximum to keep prompt schema sane.
        snapshot = snapshot[: settings.TOOLLAB_MAX_TOOLS_PER_RUN]
        snapshot.sort(key=lambda t: t.name)
        return snapshot

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, tool_name: str, args: dict, user_sub: str) -> dict:
        """Resolve ``tool_name`` for ``user_sub`` and run its handler.

        Returns the structured exec_result from
        :func:`sandbox.run_handler_with_timeout`, with an extra
        ``tool_id`` / ``name`` for the trace, and ``returns`` schema validated.

        If the model called a name we don't have, returns an error result —
        we never raise into the runner.
        """
        compiled = self._resolve_for_user(tool_name, user_sub)
        if compiled is None:
            return {
                "ok": False,
                "error_kind": "tool_not_found",
                "error": f"tool {tool_name!r} not visible to user",
                "latency_ms": 0,
                "name": tool_name,
                "tool_id": None,
            }

        exec_result = sandbox.run_handler_with_timeout(
            compiled.handler,
            args or {},
            timeout_ms=settings.TOOLLAB_HANDLER_TIMEOUT_MS,
        )

        # Validate the return schema *only* on success.
        if exec_result.get("ok"):
            err = sandbox.validate_return(
                exec_result.get("result"), compiled.returns
            )
            if err is not None:
                exec_result = {
                    "ok": False,
                    "error_kind": err.get("error_kind", "return_schema_invalid"),
                    "error": err.get("message", ""),
                    "latency_ms": exec_result.get("latency_ms", 0),
                }

        exec_result["name"] = compiled.name
        exec_result["tool_id"] = str(compiled.tool_id)
        return exec_result

    def _resolve_for_user(self, tool_name: str,
                          user_sub: str) -> Optional[CompiledTool]:
        """Find an *active*, *visible* tool by name."""
        # Prefer the user's own tool over the system tool when names collide.
        own = self._by_owner_name.get((user_sub, tool_name))
        if own is not None:
            t = self._by_id.get(own)
            if t is not None:
                return t
        sys = self._by_owner_name.get((SYSTEM_OWNER, tool_name))
        if sys is not None:
            return self._by_id.get(sys)
        # Fall back to a public tool owned by another user that happens to
        # share this name. We scan the dict because (owner, name) keys are
        # per-owner, and the caller doesn't know the owner.
        for t in self._by_id.values():
            if t.name == tool_name and t.is_public:
                return t
        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: Optional[ToolRegistry] = None
_singleton_lock = threading.Lock()


def get_registry() -> ToolRegistry:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ToolRegistry()
    return _singleton
