"""Rate limiting for helm endpoints (DoS mitigation)."""

from __future__ import annotations

import asyncio
import time
from typing import List

from fastapi import Depends, HTTPException, Request

WINDOW_SECONDS = 1.0


class HelmRateLimiter:
    """Global rate limiter for helm API (sliding 1s window)."""

    def __init__(self, requests_per_second: int = 30):
        self._limit = requests_per_second
        self._timestamps: List[float] = []
        self._lock = asyncio.Lock()

    async def check(self, request: Request) -> None:
        """Raise 429 if global rate limit exceeded."""
        async with self._lock:
            now = time.monotonic()
            cutoff = now - WINDOW_SECONDS
            self._timestamps = [t for t in self._timestamps if t > cutoff]

            if len(self._timestamps) >= self._limit:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests; try again later",
                )

            self._timestamps.append(now)


def get_helm_rate_limiter(request: Request) -> HelmRateLimiter:
    """Dependency that returns the app's helm rate limiter."""
    return request.app.state.helm_rate_limiter


async def require_rate_limit(
    request: Request,
    limiter: HelmRateLimiter = Depends(get_helm_rate_limiter),
) -> None:
    """FastAPI dependency: enforce rate limit before processing request."""
    await limiter.check(request)
