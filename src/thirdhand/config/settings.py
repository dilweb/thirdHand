"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Bot
    BOT_TOKEN: str = ""
    ADMIN_IDS: list[int] = []

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://thirdhand:thirdhand@localhost:5432/thirdhand"
    DATABASE_URL_SYNC: str = "postgresql://thirdhand:thirdhand@localhost:5432/thirdhand"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # LLM
    OPENROUTER_API_KEY: str = ""
    DEFAULT_MODEL: str = "anthropic/claude-sonnet-4"

    # Search
    TAVILY_API_KEY: str = ""

    # Timezone
    DEFAULT_TIMEZONE: str = "Asia/Almaty"

    # Memory
    REDIS_HISTORY_TTL_HOURS: int = 72  # 3 days
    MAX_CONTEXT_TOKENS: int = 50_000
    MAX_SESSION_SUMMARIES: int = 30
    MAX_HISTORY_MESSAGES: int = 20


settings = Settings()
