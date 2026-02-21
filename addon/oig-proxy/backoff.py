"""Backoff utility for connection retry logic."""


class BackoffStrategy:
    """Backoff strategy for connection retries."""

    def __init__(
        self,
        max_retries: int = 3,
        initial_backoff_s: float = 1.0,
        max_backoff_s: float = 10.0,
        backoff_multiplier: float = 2.0,
    ):
        """Initialize backoff strategy."""
        self.max_retries = max_retries
        self.initial_backoff_s = initial_backoff_s
        self.max_backoff_s = max_backoff_s
        self.backoff_multiplier = backoff_multiplier
        self._attempt = 0

    def get_backoff_delay(self) -> float:
        """Get delay before next attempt."""
        delay = self.initial_backoff_s * (
            self.backoff_multiplier ** self._attempt
        )
        return min(delay, self.max_backoff_s)

    def record_failure(self) -> None:
        """Record a failure attempt."""
        self._attempt += 1

    def reset(self) -> None:
        """Reset backoff counter."""
        self._attempt = 0

    def should_retry(self) -> bool:
        """Check if we should retry."""
        return self._attempt < self.max_retries
