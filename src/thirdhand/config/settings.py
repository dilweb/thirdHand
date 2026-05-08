"""Application settings loaded from environment variables."""

from pydantic import Field

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignore unknown env keys so deprecated vars (e.g. removed HH_*) in local .env do not crash startup.
        extra="ignore",
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
    REASONING_MODEL: str = ""
    INTENT_MODEL: str = ""
    BROWSER_MODEL: str = ""
    CHAT_MODEL: str = ""
    SEARCH_MODEL: str = ""
    PROFILE_MODEL: str = ""
    SUMMARY_MODEL: str = ""
    PICTURE_RECOGNITION_MODEL: str = ""

    # Search
    TAVILY_API_KEY: str = ""
    SEARCH_MAX_RESULTS: int = 5

    # Browser automation
    BROWSER_HEADLESS: bool = False
    BROWSER_PROFILE_DIR: str = ".browser-profile"
    BROWSER_MAX_STEPS: int = 18
    BROWSER_SNAPSHOT_TEXT_LIMIT: int = 4000
    # Upper bound how long `ainvoke` may run for one browser-agent LLM step (includes slow providers).
    BROWSER_LLM_STEP_TIMEOUT_SECONDS: int = Field(default=690, ge=120)
    BROWSER_LLM_STEP_HEARTBEAT_SECONDS: int = Field(default=10, ge=3)
    # Cap on interactive elements emitted by inspect_page; larger lists surface listing CTAs (e.g. apply links).
    BROWSER_INSPECT_INTERACTIVE_LIMIT: int = Field(default=180, ge=60, le=600)
    # When False (default), Telegram browser run summaries use a compact tool trace.
    BROWSER_REPORT_VERBOSE: bool = False
    # Deprecated compatibility fields. The runtime no longer uses the remote browser flow,
    # but older .env files may still contain these variables.
    BROWSER_REMOTE_LOGIN_URL: str = ""
    BROWSER_LOGIN_START_URL: str = ""
    BROWSER_SITE_CREDENTIALS_JSON: str = ""

    # Timezone
    DEFAULT_TIMEZONE: str = "Asia/Almaty"

    # Memory
    REDIS_HISTORY_TTL_HOURS: int = 72  # 3 days
    MAX_CONTEXT_TOKENS: int = 50_000
    MAX_SESSION_SUMMARIES: int = 30
    MAX_HISTORY_MESSAGES: int = 20


settings = Settings()
