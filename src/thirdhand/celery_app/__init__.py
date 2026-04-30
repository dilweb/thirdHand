"""Celery application initialization."""

from celery import Celery

from src.thirdhand.config import settings
from src.thirdhand.celery_app.config import CELERY_BEAT_SCHEDULE


def create_celery_app() -> Celery:
    """Create and configure the Celery application.

    Returns:
        Configured Celery instance.
    """
    app = Celery(
        "thirdhand",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )

    # Configuration
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Almaty",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        beat_schedule=CELERY_BEAT_SCHEDULE,
    )

    # Auto-discover tasks
    app.autodiscover_tasks(
        ["src.thirdhand.celery_app"],
        related_name="tasks",
    )

    return app


celery_app = create_celery_app()
