"""LLM service wrapper for OpenRouter with retry and error handling."""

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from langchain_openai import ChatOpenAI

from src.thirdhand.config import settings

logger = structlog.get_logger(__name__)


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
        max_retries=2,
        request_timeout=30,
    )


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((Exception,)),
    reraise=True,
)
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
            error=str(e),
            error_type=type(e).__name__,
            input_preview=str(input_data)[:200],
        )
        return fallback
