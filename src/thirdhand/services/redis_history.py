r"""Redis history service for storing conversation history with TTL."""

import json
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from src.thirdhand.config import settings


def _get_redis_client() -> redis.Redis:
    """Get async Redis client."""
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _history_key(user_id: int) -> str:
    """Redis key for user's conversation history."""
    return f"user:{user_id}:history"


def _pending_task_key(user_id: int) -> str:
    """Redis key for user's unresolved pending task."""
    return f"user:{user_id}:pending_task"


async def push_message(user_id: int, role: str, content: str) -> int:
    """Push a message to user's history with TTL.

    Args:
        user_id: Telegram user ID.
        role: "user" or "assistant".
        content: Message content.

    Returns:
        New length of the history list.
    """
    client = _get_redis_client()
    key = _history_key(user_id)

    entry = json.dumps(
        {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )

    async with client as r:
        await r.rpush(key, entry)
        ttl_seconds = settings.REDIS_HISTORY_TTL_HOURS * 3600
        await r.expire(key, ttl_seconds)
        length = await r.llen(key)

    return length


async def get_history(user_id: int, limit: int | None = None) -> list[dict[str, Any]]:
    """Get user's conversation history.

    Args:
        user_id: Telegram user ID.
        limit: Maximum number of messages to return (from the end).

    Returns:
        List of message dicts with role, content, timestamp.
    """
    client = _get_redis_client()
    key = _history_key(user_id)

    async with client as r:
        if limit:
            entries = await r.lrange(key, -limit, -1)
        else:
            entries = await r.lrange(key, 0, -1)

    return [json.loads(e) for e in entries]


async def clear_history(user_id: int) -> bool:
    """Clear user's conversation history.

    Args:
        user_id: Telegram user ID.

    Returns:
        True if key was deleted.
    """
    client = _get_redis_client()
    key = _history_key(user_id)

    async with client as r:
        return await r.delete(key) > 0


async def get_and_clear_history(user_id: int) -> list[dict[str, Any]]:
    """Atomically get and clear user's history.

    Used when TTL expires and we need to summarize the history.

    Args:
        user_id: Telegram user ID.

    Returns:
        List of message dicts.
    """
    history = await get_history(user_id)
    await clear_history(user_id)
    return history


async def set_pending_task(user_id: int, task: dict[str, Any]) -> None:
    """Persist a pending task that should be resumed on the next user message."""
    client = _get_redis_client()
    key = _pending_task_key(user_id)

    async with client as r:
        await r.set(key, json.dumps(task, ensure_ascii=False))
        ttl_seconds = settings.REDIS_HISTORY_TTL_HOURS * 3600
        await r.expire(key, ttl_seconds)


async def get_pending_task(user_id: int) -> dict[str, Any] | None:
    """Load the current pending task for a user, if any."""
    client = _get_redis_client()
    key = _pending_task_key(user_id)

    async with client as r:
        raw = await r.get(key)

    return json.loads(raw) if raw else None


async def clear_pending_task(user_id: int) -> bool:
    """Clear the user's pending task."""
    client = _get_redis_client()
    key = _pending_task_key(user_id)

    async with client as r:
        return await r.delete(key) > 0


async def clear_session_redis(user_id: int) -> tuple[bool, bool]:
    """Clear conversation history and pending task for one user (Telegram session reset)."""
    history_cleared = await clear_history(user_id)
    pending_cleared = await clear_pending_task(user_id)
    return history_cleared, pending_cleared
