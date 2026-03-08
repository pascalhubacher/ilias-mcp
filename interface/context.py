"""
Interface layer — shared application context and rate limiter.
"""

import os
import time
from dataclasses import dataclass, field

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from application.auth_service import AuthService
from application.course_service import CourseService
from application.download_service import DownloadService, DownloadTracker
from domain.models import LoginCredentials

_RATE_LIMIT_CALLS = int(os.environ.get("RATE_LIMIT_CALLS_PER_MIN", "20"))
_MIN_INTERVAL = 60.0 / _RATE_LIMIT_CALLS


@dataclass
class RateLimiter:
    _last_calls: dict[str, float] = field(default_factory=dict)

    def check(self, tool_name: str) -> None:
        now = time.monotonic()
        last = self._last_calls.get(tool_name, 0.0)
        elapsed = now - last
        if elapsed < _MIN_INTERVAL:
            wait = _MIN_INTERVAL - elapsed
            raise RuntimeError(
                f"Rate limit exceeded for '{tool_name}'. "
                f"Please wait {wait:.1f}s before calling it again."
            )
        self._last_calls[tool_name] = now


@dataclass
class AppContext:
    credentials: LoginCredentials
    auth_service: AuthService
    course_service: CourseService
    download_service: DownloadService
    rate_limiter: RateLimiter
    download_tracker: DownloadTracker = field(default_factory=DownloadTracker)


def app_from_ctx(ctx: Context[ServerSession, "AppContext"]) -> "AppContext":
    return ctx.request_context.lifespan_context
