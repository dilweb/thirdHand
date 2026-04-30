"""Celery Beat schedule configuration."""

from celery.schedules import crontab

from src.thirdhand.config import settings

# Beat schedule
CELERY_BEAT_SCHEDULE = {
    "periodic_interest_search": {
        "task": "src.thirdhand.celery_app.tasks.periodic_interest_search",
        "schedule": crontab(hour=9, minute=0),  # Every day at 9:00
        "options": {"expires": 3600},
    },
    "cleanup_old_reminders": {
        "task": "src.thirdhand.celery_app.tasks.cleanup_old_reminders",
        "schedule": crontab(hour=0, minute=0),  # Every day at 00:00
        "options": {"expires": 3600},
    },
}
