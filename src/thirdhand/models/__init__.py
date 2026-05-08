"""SQLAlchemy models for the application."""

from .base import Base, TimestampMixin
from .user import User
from .reminder import Reminder, ReminderStatus
from .interest import Interest
from .profile import UserProfile
from .search_digest import SearchDigest
from .database import (
    engine,
    async_session_factory,
    sync_engine,
    sync_session_factory,
    get_async_session,
    get_session,
    get_sync_session,
    init_db,
    close_db,
)
from .queries import (
    UserQueries,
    ReminderQueries,
    InterestQueries,
    UserProfileQueries,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "User",
    "Reminder",
    "ReminderStatus",
    "Interest",
    "UserProfile",
    "SearchDigest",
    "engine",
    "async_session_factory",
    "sync_engine",
    "sync_session_factory",
    "get_async_session",
    "get_session",
    "get_sync_session",
    "init_db",
    "close_db",
    "UserQueries",
    "ReminderQueries",
    "InterestQueries",
    "UserProfileQueries",
]
