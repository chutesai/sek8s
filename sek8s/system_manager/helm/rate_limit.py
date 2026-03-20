"""Rate limiting for helm endpoints (DoS mitigation)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Dict, List

from fastapi import Depends, HTTPException, Request

WINDOW_SECONDS = 60.0


class HelmRateLimiter:
    """Per-IP rate limiter for helm API (sliding window)."""

    def __init__(self, requests_per_minute: int = 60):
        self._limit = requests_per_minute
        self._windows: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, request: Request) -> None:
        """Raise 429 if client has exceeded the rate limit."""
        client = request.client
        ip = client.host if client else "unknown"

        async with self._lock:
            now = time.monotonic()
            cutoff = now - WINDOW_SECONDS
            self._windows[ip] = [t for t in self._windows[ip] if t > cutoff]

            if len(self._windows[ip]) >= self._limit:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests; try again later",
                )

            self._windows[ip].append(now)


def get_helm_rate_limiter(request: Request) -> HelmRateLimiter:
    """Dependency that returns the app's helm rate limiter."""
    return request.app.state.helm_rate_limiter


async def require_rate_limit(
    request: Request,
    limiter: HelmRateLimiter = Depends(get_helm_rate_limiter),
) -> None:
    """FastAPI dependency: enforce rate limit before processing request."""
    await limiter.check(request)
