"""Async end-to-start timestamp rate limiter for external HTTP clients."""

import asyncio
import time
from types import TracebackType
from typing import Self


class AsyncRateLimiter:
    """Enforce a minimum end-to-start cooldown interval between async operations."""

    def __init__(self, delay: float) -> None:
        """Initialize the rate limiter with a minimum ``delay`` in seconds."""
        self.delay = delay
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def __aenter__(self) -> Self:
        """Acquire the limiter lock and sleep for any remaining cooldown window."""
        if self.delay <= 0:
            return self
        await self._lock.acquire()
        try:
            now = time.monotonic()
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
        except BaseException:
            self._lock.release()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stamp the next allowed timestamp on clean exit and release the lock."""
        if self.delay <= 0:
            return
        try:
            if exc_type is None:
                self._next_allowed = time.monotonic() + self.delay
        finally:
            self._lock.release()
