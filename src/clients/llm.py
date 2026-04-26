"""LLM client using Anthropic (Claude) only."""

from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Callable, TypeVar

T = TypeVar("T")


def _retry_transient(
    fn: Callable[[], T],
    *,
    retriable: tuple[type[BaseException], ...],
    max_attempts: int = 3,
    base_delay: float = 2.0,
) -> T:
    delay = base_delay
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return fn()
        except retriable as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                break
            time.sleep(delay)
            delay = min(delay * 2, 16.0)

    assert last_exc is not None
    raise last_exc


def model_id() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")


@lru_cache(maxsize=4)
def _anthropic_client():
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    return anthropic.Anthropic(api_key=api_key)


def complete(system: str, user: str) -> str:
    import anthropic

    client = _anthropic_client()

    retriable = (
        anthropic.RateLimitError,
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.InternalServerError,
    )

    msg = _retry_transient(
        lambda: client.messages.create(
            model=model_id(),
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        ),
        retriable=retriable,
    )

    parts = [
        block.text
        for block in msg.content
        if getattr(block, "type", None) == "text"
    ]

    return "".join(parts).strip()