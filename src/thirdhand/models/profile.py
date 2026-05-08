"""UserProfile model for storing extended user preferences and context."""

from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class UserProfile(Base, TimestampMixin):
    """Extended user profile with preferences and context summary.

    Stores:
    - context_summary: Compressed user profile (< 5K tokens)
    - session_summaries: Array of session summaries (< 30K tokens)
    - estimated_tokens: Approximate token count for monitoring
    """

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="Telegram user ID",
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment="User preferences (notifications_time, timezone, etc.)",
    )
    context_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment="LLM-extracted context (occupation, stack, interests, etc.)",
    )
    session_summaries: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        comment="Array of session summaries (compressed on TTL expire)",
    )
    estimated_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Estimated token count for context monitoring",
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="profile",
    )

    def __repr__(self) -> str:
        return (
            f"<UserProfile(id={self.id}, user_id={self.user_id}, tokens={self.estimated_tokens})>"
        )
