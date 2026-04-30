"""SearchDigest model for storing history of sent search digests."""

from typing import Any, Optional

from sqlalchemy import BigInteger, ForeignKey, Identity, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class SearchDigest(Base, TimestampMixin):
    """History of search results sent to users."""

    __tablename__ = "search_digests"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Telegram user ID",
    )
    results: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Search results data",
    )
    source_topic: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Topic that triggered this search",
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="search_digests",
    )

    def __repr__(self) -> str:
        return (
            f"<SearchDigest(id={self.id}, user_id={self.user_id}, "
            f"source_topic={self.source_topic!r})>"
        )
