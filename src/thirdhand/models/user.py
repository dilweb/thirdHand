"""User model for storing Telegram user information."""

from typing import Optional

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """Telegram user model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="Telegram user ID",
    )
    username: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    first_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    last_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    language_code: Mapped[str] = mapped_column(
        String(10),
        default="ru",
        nullable=False,
    )

    # Relationships
    reminders: Mapped[list["Reminder"]] = relationship(
        "Reminder",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    interests: Mapped[list["Interest"]] = relationship(
        "Interest",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    profile: Mapped[Optional["UserProfile"]] = relationship(
        "UserProfile",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    search_digests: Mapped[list["SearchDigest"]] = relationship(
        "SearchDigest",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username!r}, first_name={self.first_name!r})>"
