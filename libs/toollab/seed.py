"""Seed tools — 4 atomic + 1 macro — autoloaded at startup.

The seed tools share an in-memory ``_USERS`` dict (process-local; reset on
container restart, which is intentional for the demo). The dict is injected
into the sandbox of *system-owned* handlers via
:meth:`ToolRegistry.set_system_extras`. User-registered tools never see it.

The macro ``onboard_new_team`` exists deliberately so we can demonstrate the
§RQ §1 hypothesis (atomic-only carries weak models, atomic+macro lifts them):
with the macro active, even a small model can finish the onboarding scenario
in a single tool call.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.toollab.models import ToolDefinition
from libs.toollab.registry import SYSTEM_OWNER, get_registry

logger = logging.getLogger("llm-chat-agent.toollab.seed")

# Process-local in-memory store shared by all seed handlers.
_USERS: dict[str, dict] = {}


def _new_uid() -> str:
    return f"u-{_uuid.uuid4().hex[:8]}"


SEED_TOOLS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # 1. list_users (atomic)
    # ------------------------------------------------------------------
    {
        "name": "list_users",
        "description": "List all currently registered users (id, name, email, group).",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "returns": {
            "type": "object",
            "properties": {
                "users": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "group": {"type": ["string", "null"]},
                        },
                        "required": ["id", "name", "email"],
                    },
                }
            },
            "required": ["users"],
        },
        "code": (
            "def handler():\n"
            "    return {'users': list(_USERS.values())}\n"
        ),
        "tags": ["seed", "atomic"],
    },
    # ------------------------------------------------------------------
    # 2. get_user (atomic)
    # ------------------------------------------------------------------
    {
        "name": "get_user",
        "description": "Get a single user by id. Returns null in 'user' if not found.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "returns": {
            "type": "object",
            "properties": {
                "user": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "email": {"type": "string"},
                                "group": {"type": ["string", "null"]},
                            },
                            "required": ["id", "name", "email"],
                        },
                    ]
                }
            },
            "required": ["user"],
        },
        "code": (
            "def handler(id):\n"
            "    return {'user': _USERS.get(id)}\n"
        ),
        "tags": ["seed", "atomic"],
    },
    # ------------------------------------------------------------------
    # 3. create_user (atomic)
    # ------------------------------------------------------------------
    {
        "name": "create_user",
        "description": (
            "Create a new user with the given name and email. Returns the created user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "group": {"type": ["string", "null"]},
            },
            "required": ["name", "email"],
            "additionalProperties": False,
        },
        "returns": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "group": {"type": ["string", "null"]},
                    },
                    "required": ["id", "name", "email"],
                }
            },
            "required": ["user"],
        },
        "code": (
            "def handler(name, email, group=None):\n"
            "    # naive id generation — fine for demo (process-local)\n"
            "    new_id = 'u-' + str(len(_USERS) + 1).zfill(5)\n"
            "    user = {'id': new_id, 'name': name, 'email': email, 'group': group}\n"
            "    _USERS[new_id] = user\n"
            "    return {'user': user}\n"
        ),
        "tags": ["seed", "atomic"],
    },
    # ------------------------------------------------------------------
    # 4. update_user_email (atomic)
    # ------------------------------------------------------------------
    {
        "name": "update_user_email",
        "description": "Update an existing user's email by id.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "email": {"type": "string"},
            },
            "required": ["id", "email"],
            "additionalProperties": False,
        },
        "returns": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "user": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "email": {"type": "string"},
                                "group": {"type": ["string", "null"]},
                            },
                            "required": ["id", "name", "email"],
                        },
                    ]
                },
            },
            "required": ["ok", "user"],
        },
        "code": (
            "def handler(id, email):\n"
            "    u = _USERS.get(id)\n"
            "    if u is None:\n"
            "        return {'ok': False, 'user': None}\n"
            "    u['email'] = email\n"
            "    return {'ok': True, 'user': u}\n"
        ),
        "tags": ["seed", "atomic"],
    },
    # ------------------------------------------------------------------
    # 5. onboard_new_team (macro)
    # ------------------------------------------------------------------
    {
        "name": "onboard_new_team",
        "description": (
            "Create users, assign each to a group, and prepare a welcome "
            "message — all in one call. Use this when the user asks you to "
            "'onboard a team' or similar end-to-end workflow."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "team": {"type": "string"},
                "group": {"type": "string"},
                "members": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                        },
                        "required": ["name", "email"],
                    },
                },
            },
            "required": ["team", "group", "members"],
            "additionalProperties": False,
        },
        "returns": {
            "type": "object",
            "properties": {
                "team": {"type": "string"},
                "group": {"type": "string"},
                "created": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "group": {"type": "string"},
                            "welcome": {"type": "string"},
                        },
                        "required": ["id", "name", "email", "group", "welcome"],
                    },
                },
            },
            "required": ["team", "group", "created"],
        },
        "code": (
            "def handler(team, group, members):\n"
            "    created = []\n"
            "    for m in members:\n"
            "        uid = 'u-' + str(len(_USERS) + 1).zfill(5)\n"
            "        user = {\n"
            "            'id': uid,\n"
            "            'name': m['name'],\n"
            "            'email': m['email'],\n"
            "            'group': group,\n"
            "        }\n"
            "        _USERS[uid] = user\n"
            "        created.append({\n"
            "            **user,\n"
            "            'welcome': 'Welcome to ' + team + ', ' + m['name'] + '!',\n"
            "        })\n"
            "    return {'team': team, 'group': group, 'created': created}\n"
        ),
        "tags": ["seed", "macro"],
    },
]


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


async def bootstrap(db: AsyncSession) -> None:
    """Hydrate the registry at startup.

    Steps (all idempotent — safe to call on every container start):

    1. Inject ``_USERS`` into the system-tool sandbox.
    2. Ensure each seed tool exists in DB (insert if missing).
    3. Compile *every* active tool (system + user) into the in-memory registry.
    """
    registry = get_registry()
    registry.set_system_extras({"_USERS": _USERS})

    # 1b. Lightweight idempotent migration — Base.metadata.create_all() does not
    # add columns to existing tables, so we ensure is_public exists for legacy
    # tool_definitions rows. Safe to run on every startup.
    await db.execute(text(
        "ALTER TABLE tool_definitions "
        "ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    # System tools are runtime-visible to everyone regardless of is_public,
    # but we normalise the column value so admin views aren't confusing.
    await db.execute(text(
        "UPDATE tool_definitions SET is_public = TRUE "
        "WHERE owner_user_id = 'system' AND is_public = FALSE"
    ))
    await db.commit()

    # 2. Seed rows
    for tdict in SEED_TOOLS:
        existing = await db.execute(
            select(ToolDefinition).where(
                ToolDefinition.owner_user_id == SYSTEM_OWNER,
                ToolDefinition.name == tdict["name"],
                ToolDefinition.is_active.is_(True),
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue
        td = ToolDefinition(
            owner_user_id=SYSTEM_OWNER,
            name=tdict["name"],
            version=1,
            description=tdict["description"],
            parameters_json=tdict["parameters"],
            returns_json=tdict["returns"],
            code=tdict["code"],
            tags=tdict["tags"],
            is_active=True,
            # System tools are visible to everyone regardless; setting True
            # makes the row's status consistent with the runtime semantic.
            is_public=True,
        )
        db.add(td)
    await db.commit()

    # 3. Compile every active tool — system + user
    rows = await db.execute(
        select(ToolDefinition).where(ToolDefinition.is_active.is_(True))
    )
    count = 0
    for td in rows.scalars().all():
        try:
            registry.register(td)
            count += 1
        except Exception as e:  # noqa: BLE001 — logged, run continues
            logger.exception(
                "tool registration failed: name=%s id=%s err=%s",
                td.name, td.id, e,
            )
    logger.info("toollab.bootstrap registered %d tool(s)", count)
