"""Deterministic waiting.

The rule this module exists to enforce: **never sleep for a fixed period**.

`time.sleep(5)` is slow when the condition is already true and flaky when the
machine is loaded. Every wait here polls a real condition and fails with a
message describing what it was waiting for, so a timeout is diagnosable rather
than mysterious.

Polling is only appropriate for genuinely asynchronous state - a service coming
up, a background effect landing. Where the API is synchronous, tests assert on
the response directly and use nothing from this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_TIMEOUT = 30.0
DEFAULT_INTERVAL = 0.25


class ConditionNotMetError(AssertionError):
    """Raised when a polled condition does not become true in time."""


def wait_until(
    condition: Callable[[], bool],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    description: str = "condition",
) -> None:
    """Poll ``condition`` until it returns True, or fail with context."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except Exception as exc:
            last_error = exc
        time.sleep(interval)

    suffix = f" Last error: {type(last_error).__name__}: {last_error}" if last_error else ""
    raise ConditionNotMetError(f"Timed out after {timeout}s waiting for {description}.{suffix}")


def wait_for_value(
    supplier: Callable[[], T],
    predicate: Callable[[T], bool],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    description: str = "value",
) -> T:
    """Poll ``supplier`` until its result satisfies ``predicate``; return it.

    Reports the last value seen on timeout, which usually identifies the problem
    immediately (for example "waiting for status 'shipped', last saw 'pending'").
    """
    deadline = time.monotonic() + timeout
    last_value: T | None = None
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            last_value = supplier()
            if predicate(last_value):
                return last_value
        except Exception as exc:
            last_error = exc
        time.sleep(interval)

    if last_error is not None:
        raise ConditionNotMetError(
            f"Timed out after {timeout}s waiting for {description}. "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )
    raise ConditionNotMetError(
        f"Timed out after {timeout}s waiting for {description}. Last value: {last_value!r}"
    )


def wait_for_service(
    check: Callable[[], bool],
    *,
    timeout: float = 90.0,
    interval: float = 1.0,
    name: str = "service",
) -> None:
    """Wait for a dependency to become reachable.

    Used by the Makefile/CI entry points so the suite starts the moment the
    stack is ready, instead of after a fixed sleep long enough to "probably" be
    safe.
    """
    wait_until(check, timeout=timeout, interval=interval, description=f"{name} to become ready")
