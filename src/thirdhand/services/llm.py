"""LLM service wrapper for OpenRouter with retry and error handling."""

import json
import re
from collections.abc import Awaitable, Callable
from functools import wraps

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from langchain_openai import ChatOpenAI

from src.thirdhand.config import settings

logger = structlog.get_logger(__name__)


def redact_sensitive_text_for_log(text: str, *, limit: int = 120) -> str:
    """Mask phone-like sequences and trim for safe structured logs."""
    if not text:
        return ""

    s = " ".join(str(text).split())
    s = re.sub(r"(?:\+?7|8)[\s\-()]?\d(?:[\s\-()]*\d){9,}", "[PHONE_REDACTED]", s)
    s = re.sub(r"\b\d{10,16}\b", "[DIGITS_REDACTED]", s)
    return s if len(s) <= limit else f"{s[: limit - 1]}…"


def preview_for_log(value, limit: int = 600) -> str:
    """Serialize and truncate values for readable structured logs."""
    if isinstance(value, str):
        normalized = " ".join(value.split())
    else:
        try:
            normalized = json.dumps(value, ensure_ascii=False)
        except Exception:
            normalized = str(value)
        normalized = " ".join(normalized.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}…"


def create_llm(
    model: str | None = None,
    temperature: float = 0.0,
) -> ChatOpenAI:
    """Create a ChatOpenAI instance configured for OpenRouter.

    Args:
        model: Model name (defaults to settings.DEFAULT_MODEL).
        temperature: Sampling temperature.

    Returns:
        Configured ChatOpenAI instance.
    """
    return ChatOpenAI(
        model=model or settings.DEFAULT_MODEL,
        openai_api_key=settings.OPENROUTER_API_KEY,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=temperature,
        max_retries=0,
        request_timeout=30,
    )


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """Return True when the failure is worth retrying with backoff."""
    if isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    return False


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """Log before a retry sleep starts."""
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome else None
    next_sleep = getattr(retry_state.next_action, "sleep", None)
    logger.warning(
        "llm_retry_scheduled",
        attempt=retry_state.attempt_number,
        next_sleep_seconds=round(next_sleep, 2) if next_sleep is not None else None,
        error_type=type(exc).__name__ if exc else None,
        error=str(exc) if exc else None,
    )


def llm_retry_async(
    *,
    attempts: int = 4,
    min_wait_seconds: float = 1.0,
    max_wait_seconds: float = 20.0,
) -> Callable[[Callable[..., Awaitable]], Callable[..., Awaitable]]:
    """Decorator for async LLM calls with jittered exponential backoff."""

    def decorator(func: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
        @retry(
            stop=stop_after_attempt(attempts),
            wait=wait_random_exponential(multiplier=1, min=min_wait_seconds, max=max_wait_seconds),
            retry=retry_if_exception(_is_retryable_llm_error),
            before_sleep=_log_retry_attempt,
            reraise=True,
        )
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def llm_retry_sync(
    *,
    attempts: int = 4,
    min_wait_seconds: float = 1.0,
    max_wait_seconds: float = 20.0,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Decorator for sync LLM calls with jittered exponential backoff."""

    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        @retry(
            stop=stop_after_attempt(attempts),
            wait=wait_random_exponential(multiplier=1, min=min_wait_seconds, max=max_wait_seconds),
            retry=retry_if_exception(_is_retryable_llm_error),
            before_sleep=_log_retry_attempt,
            reraise=True,
        )
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator


@llm_retry_sync()
def invoke_with_retry(chain, input_data: dict) -> dict:
    """Invoke an LLM chain with retry logic.

    Args:
        chain: LangChain chain to invoke.
        input_data: Input data for the chain.

    Returns:
        Chain output.

    Raises:
        Exception: If all retries fail.
    """
    return chain.invoke(input_data)


@llm_retry_async()
async def ainvoke_with_retry(chain, input_data):
    """Invoke an async LLM chain with retry logic."""
    return await chain.ainvoke(input_data)


def safe_invoke(chain, input_data: dict, fallback=None):
    """Safely invoke an LLM chain with error handling.

    Args:
        chain: LangChain chain to invoke.
        input_data: Input data for the chain.
        fallback: Fallback result to return on failure.

    Returns:
        Chain output or fallback on failure.
    """
    try:
        return invoke_with_retry(chain, input_data)
    except Exception as e:
        logger.error(
            "llm_invocation_failed",
            model=getattr(getattr(chain, "model", None), "model_name", None),
            error=str(e),
            error_type=type(e).__name__,
            retryable=_is_retryable_llm_error(e),
            input_preview=preview_for_log(input_data, limit=400),
        )
        return fallback
