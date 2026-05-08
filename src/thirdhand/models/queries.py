"""Common database query helpers."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from .interest import Interest
from .profile import UserProfile
from .reminder import Reminder, ReminderStatus
from .user import User


class UserQueries:
    """Queries for User model."""

    @staticmethod
    async def get_or_create(
        session: AsyncSession,
        user_id: int,
        defaults: dict[str, Any] | None = None,
    ) -> tuple[User, bool]:
        """Get user by ID or create if not exists.

        Returns:
            Tuple of (user, created) where created is True if user was created.
        """
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user:
            return user, False

        defaults = defaults or {}
        user = User(id=user_id, **defaults)
        session.add(user)
        await session.flush()
        return user, True

    @staticmethod
    @staticmethod
    async def update_from_telegram(
        session: AsyncSession,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> User:
        """Update user info from Telegram."""
        user, created = await UserQueries.get_or_create(session, user_id)

        if created:
            # Set initial values on newly created user
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
            if last_name is not None:
                user.last_name = last_name
            if language_code is not None:
                user.language_code = language_code
        else:
            # Update existing user
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
            if last_name is not None:
                user.last_name = last_name
            if language_code is not None:
                user.language_code = language_code

        await session.flush()
        return user


class ReminderQueries:
    """Queries for Reminder model."""

    @staticmethod
    async def create_reminder(
        session: AsyncSession,
        user_id: int,
        title: str,
        remind_at: Any,  # datetime
        description: str | None = None,
    ) -> Reminder:
        """Create a new reminder."""
        reminder = Reminder(
            user_id=user_id,
            title=title,
            description=description,
            remind_at=remind_at,
        )
        session.add(reminder)
        await session.flush()
        return reminder

    @staticmethod
    async def get_pending_reminders(
        session: AsyncSession,
    ) -> list[Reminder]:
        """Get all pending reminders."""
        result = await session.execute(
            select(Reminder)
            .where(Reminder.status == ReminderStatus.PENDING)
            .order_by(Reminder.remind_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_user_reminders(
        session: AsyncSession,
        user_id: int,
    ) -> list[Reminder]:
        """Get all reminders for a user."""
        result = await session.execute(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.status == ReminderStatus.PENDING,
            )
            .order_by(Reminder.remind_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def mark_as_sent(
        session: AsyncSession,
        reminder_id: int,
        celery_task_id: str | None = None,
    ) -> None:
        """Mark reminder as sent."""
        result = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        reminder = result.scalar_one_or_none()
        if reminder:
            reminder.status = ReminderStatus.SENT
            if celery_task_id:
                reminder.celery_task_id = celery_task_id

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        reminder_id: int,
    ) -> Reminder | None:
        """Fetch one reminder by ID."""
        result = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        return result.scalar_one_or_none()

    @staticmethod
    def get_by_id_sync(
        session: Session,
        reminder_id: int,
    ) -> Reminder | None:
        """Fetch one reminder by ID using a sync session."""
        result = session.execute(select(Reminder).where(Reminder.id == reminder_id))
        return result.scalar_one_or_none()

    @staticmethod
    def mark_as_sent_sync(
        session: Session,
        reminder_id: int,
        celery_task_id: str | None = None,
    ) -> None:
        """Mark reminder as sent using a sync session."""
        result = session.execute(select(Reminder).where(Reminder.id == reminder_id))
        reminder = result.scalar_one_or_none()
        if reminder:
            reminder.status = ReminderStatus.SENT
            if celery_task_id:
                reminder.celery_task_id = celery_task_id


class InterestQueries:
    """Queries for Interest model."""

    @staticmethod
    async def upsert(
        session: AsyncSession,
        user_id: int,
        topic: str,
        keywords: list[str] | None = None,
        priority: float = 1.0,
    ) -> Interest:
        """Upsert an interest for a user."""
        result = await session.execute(
            select(Interest).where(
                Interest.user_id == user_id,
                Interest.topic == topic,
            )
        )
        interest = result.scalar_one_or_none()

        if interest:
            if keywords is not None:
                interest.keywords = keywords
            interest.priority = priority
        else:
            interest = Interest(
                user_id=user_id,
                topic=topic,
                keywords=keywords or [],
                priority=priority,
            )
            session.add(interest)

        await session.flush()
        return interest

    @staticmethod
    async def get_user_interests(
        session: AsyncSession,
        user_id: int,
    ) -> list[Interest]:
        """Get all interests for a user."""
        result = await session.execute(
            select(Interest).where(Interest.user_id == user_id).order_by(Interest.priority.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_active(
        session: AsyncSession,
    ) -> list[Interest]:
        """Get all active interests across all users."""
        result = await session.execute(
            select(Interest).options(selectinload(Interest.user)).order_by(Interest.priority.desc())
        )
        return list(result.scalars().all())


class UserProfileQueries:
    """Queries for UserProfile model."""

    @staticmethod
    async def get_or_create(
        session: AsyncSession,
        user_id: int,
    ) -> UserProfile:
        """Get user profile or create if not exists.

        Also ensures the User record exists (creates it if missing).
        """
        result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        profile = result.scalar_one_or_none()

        if profile:
            return profile

        # Ensure User record exists to satisfy FK constraint
        user_result = await session.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()

        if user is None:
            user = User(id=user_id)
            session.add(user)
            await session.flush()

        profile = UserProfile(user_id=user_id)
        session.add(profile)
        await session.flush()
        return profile
