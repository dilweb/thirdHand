"""Reminder model for storing user reminders."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class ReminderStatus(enum.StrEnum):
    """Reminder status enumeration."""

    PENDING = "pending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


class Reminder(Base, TimestampMixin):
    """User reminder model."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Telegram user ID",
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    remind_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When to send the reminder",
    )
    status: Mapped[ReminderStatus] = mapped_column(
        String(20),
        default=ReminderStatus.PENDING,
        nullable=False,
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Celery task ID for this reminder",
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="reminders",
    )

    # Indexes
    __table_args__ = (
        Index(
            "ix_reminders_remind_at_pending",
            "remind_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Reminder(id={self.id}, user_id={self.user_id}, "
            f"title={self.title!r}, remind_at={self.remind_at!r}, "
            f"status={self.status!r})>"
        )
