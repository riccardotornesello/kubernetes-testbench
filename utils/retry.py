"""Retries with exponential backoff."""

import random
import time
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.log import StructuredLogger, get_logger

T = TypeVar("T")

log = get_logger(__name__)


class BackoffConfig(BaseModel):
    """
    Retry policy with exponential backoff: the n-th retry (n >= 1) waits
    min(initial_delay * multiplier**(n-1), max_delay) seconds, randomized by
    +/- `jitter` (fraction) so that concurrent retries do not synchronize.
    """

    model_config = ConfigDict(extra="forbid")

    # Total number of attempts (1 = no retry)
    attempts: int = Field(default=10, ge=1)
    initial_delay: float = Field(default=5.0, ge=0)
    max_delay: float = Field(default=120.0, ge=0)
    multiplier: float = Field(default=2.0, ge=1)
    jitter: float = Field(default=0.1, ge=0, le=1)

    @model_validator(mode="after")
    def _check_delays(self):
        if self.max_delay < self.initial_delay:
            raise ValueError(
                f"max_delay ({self.max_delay}) is lower than "
                f"initial_delay ({self.initial_delay})"
            )
        return self

    def delay(self, retry: int) -> float:
        """Seconds to wait before the given retry (1 = first retry)."""
        base = min(self.initial_delay * self.multiplier ** (retry - 1), self.max_delay)
        return max(0.0, base * (1 + random.uniform(-self.jitter, self.jitter)))


def retry_with_backoff(
    func: Callable[[], T],
    policy: BackoffConfig,
    retry_on: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    description: str = "operation",
    logger: StructuredLogger | None = None,
) -> T:
    """
    Calls `func` until it succeeds, at most `policy.attempts` times, waiting
    with exponential backoff between the attempts. Only the `retry_on`
    exceptions are retried; the last one is re-raised when the attempts are
    exhausted.
    """
    logger = logger or log
    for attempt in range(1, policy.attempts + 1):
        try:
            return func()
        except retry_on as e:
            if attempt == policy.attempts:
                logger.error(
                    f"{description} failed, no attempts left",
                    attempt=attempt,
                    attempts=policy.attempts,
                    error=str(e),
                )
                raise
            delay = policy.delay(attempt)
            logger.warning(
                f"{description} failed, retrying",
                attempt=attempt,
                attempts=policy.attempts,
                retry_in=f"{delay:.1f}s",
                error=str(e),
            )
            time.sleep(delay)
    raise AssertionError("unreachable")
