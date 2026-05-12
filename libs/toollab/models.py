"""SQLAlchemy ORM models for Tool Lab.

Tables are auto-created at startup via ``Base.metadata.create_all`` (see apps/api/main.py).
DDL parity reference: docs/P07_설계서.md §2.
"""
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from libs.core.database import Base


class ToolDefinition(Base):
    """Dynamically registered tool — JSON schema + Python handler body.

    ``(owner_user_id, name)`` is functionally unique among ``is_active=true`` rows;
    older versions are deactivated rather than deleted to preserve trace
    reproducibility (``ToolRun.tool_ids`` references the PK).
    """

    __tablename__ = "tool_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id = Column(Text, nullable=False, index=True)
    name = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    description = Column(Text, nullable=False)
    parameters_json = Column(JSONB, nullable=False)
    returns_json = Column(JSONB, nullable=False)
    code = Column(Text, nullable=False)
    tags = Column(ARRAY(Text), nullable=False, server_default="{}")
    is_active = Column(Boolean, nullable=False, default=True)
    # Sharing: when True, other users see this tool in the Run picker.
    # Editing/deleting still requires ownership; only visibility is widened.
    is_public = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "name", "version",
            name="uq_tool_def_owner_name_version",
        ),
        CheckConstraint(
            "name ~ '^[a-zA-Z_][a-zA-Z0-9_]{0,63}$'",
            name="ck_tool_def_name_format",
        ),
        Index(
            "ix_tool_def_owner_active",
            "owner_user_id", "is_active", "name",
        ),
    )


class ToolRun(Base):
    """One execution trace — assistant + tool messages, usage, warnings."""

    __tablename__ = "tool_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id = Column(Text, nullable=False, index=True)
    model_type = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    served_by = Column(Text, nullable=False)
    tool_call_parser = Column(Text, nullable=True)
    prompt = Column(Text, nullable=False)
    system_prompt = Column(Text, nullable=True)
    tool_ids = Column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    iterations = Column(Integer, nullable=False, default=0)
    truncated = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    trace_json = Column(JSONB, nullable=False)
    final_response = Column(Text, nullable=True)
    warnings = Column(ARRAY(Text), nullable=False, server_default="{}")
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "model_type IN ('chat', 'reasoning')",
            name="ck_tool_run_model_type",
        ),
        CheckConstraint(
            "served_by IN ('openai', 'vllm')",
            name="ck_tool_run_served_by",
        ),
        Index(
            "ix_tool_run_owner_created",
            "owner_user_id", "created_at",
        ),
    )
