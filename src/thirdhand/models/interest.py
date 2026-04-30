"""Interest model for storing user interests/topics."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Interest(Base, TimestampMixin):
    """User interest model."""

    __tablename__ = "interests"

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
    topic: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        comment="Array of keywords for this interest",
    )
    priority: Mapped[float] = mapped_column(
        Float,
        default=1.0,
        nullable=False,
        comment="Priority from 0.1 to 1.0",
    )
    last_searched: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="interests",
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint("user_id", "topic", name="uq_user_topic"),
    )

    def __repr__(self) -> str:
        return (
            f"<Interest(id={self.id}, user_id={self.user_id}, "
            f"topic={self.topic!r}, priority={self.priority})>"
        )
