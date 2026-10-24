"""Assistant audit trail (ported from Prisma `AgentRun`). One row per question:
the scope it was allowed to read (stamped at run time), the outcome, and the
trace — so "the assistant cannot read another student's record" is verifiable
after the fact. Reuses the existing `role` PG enum (create_type=False).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .user import Role


def _uuid() -> str:
    return uuid.uuid4().hex


class AgentRunStatus(str, enum.Enum):
    ANSWERED = "ANSWERED"
    EXHAUSTED = "EXHAUSTED"
    REFUSED = "REFUSED"
    FAILED = "FAILED"


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agentrun_actor_created", "actor_id", "created_at"),
        Index("ix_agentrun_status", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[Role] = mapped_column(Enum(Role, name="role", create_type=False))
    scope: Mapped[str] = mapped_column(String)  # "self" | "programme"
    question: Mapped[str] = mapped_column(String)
    answer: Mapped[str] = mapped_column(String, default="", server_default="")
    status: Mapped[AgentRunStatus] = mapped_column(Enum(AgentRunStatus, name="agent_run_status"))
    trace: Mapped[list] = mapped_column(JSONB, default=list)
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    # Grounding signal (Assistant V2 Phase D): the routed intent and whether the
    # answer was grounded in a student tool or an approved policy chunk. Nullable
    # — chat/stream runs and pre-Phase-D rows leave them null.
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    steps: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
