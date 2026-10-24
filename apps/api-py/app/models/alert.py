"""Alerts — rule-triggered flags on a student that the mentor dashboard surfaces
(ported from Prisma `Alert`). context snapshots the values that fired the rule,
for auditability; resolved_at/resolved_by close it.

AlertRuleConfig holds the admin-configurable thresholds per cohort — the rules
themselves live in data, never hard-coded — and reuses both enums here
(create_type=False).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class AlertRuleKey(str, enum.Enum):
    NO_CHECKIN_N_DAYS = "NO_CHECKIN_N_DAYS"
    PACE_BELOW_THRESHOLD = "PACE_BELOW_THRESHOLD"
    ATTENDANCE_BELOW_THRESHOLD = "ATTENDANCE_BELOW_THRESHOLD"
    CERT_OVERDUE = "CERT_OVERDUE"
    LOW_FOCUS_QUALITY = "LOW_FOCUS_QUALITY"


class AlertSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alert_student_resolved", "student_id", "resolved_at"),
        Index("ix_alert_rule", "rule_triggered"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    rule_triggered: Mapped[AlertRuleKey] = mapped_column(Enum(AlertRuleKey, name="alert_rule_key"))
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, name="alert_severity"), default=AlertSeverity.WARNING, server_default="WARNING"
    )
    message: Mapped[str] = mapped_column(String)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)


class AlertRuleConfig(Base):
    """Admin-configurable thresholds, per cohort. Never hard-coded — the mentor
    office edits `params` between intakes without a deploy. One row per
    (cohort, rule_key)."""

    __tablename__ = "alert_rule_configs"
    __table_args__ = (UniqueConstraint("cohort_id", "rule_key", name="uq_alertrule_cohort_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("cohorts.id", ondelete="CASCADE"))
    rule_key: Mapped[AlertRuleKey] = mapped_column(
        Enum(AlertRuleKey, name="alert_rule_key", create_type=False)
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # e.g. {"days": 5} or {"deviationPct": 25} or {"minAttendancePct": 75}
    params: Mapped[dict] = mapped_column(JSONB)
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, name="alert_severity", create_type=False),
        default=AlertSeverity.WARNING,
        server_default="WARNING",
    )
