"""Rate-limiting boundary for security-sensitive authentication endpoints.

auth-api.md §18 requires login and password recovery to have rate
limiting/brute-force protection with configurable parameters; §19/ADR-0009
require the same posture for registration/verification. No concrete rate
limiting infrastructure (a shared cache, Redis, a reverse-proxy layer)
exists yet in this codebase (ADR-0007 background processing and ODR-003
reverse proxy are both still open), and this Issue must not invent
undocumented numeric limits or bring in new infrastructure on its own.

This module is the seam a real implementation plugs into later: every
security-sensitive endpoint below already depends on `RateLimiter`, so
adding real throttling later is a one-line change (swap the dependency
override) with no endpoint-signature changes. `NullRateLimiter` — the
default — never blocks a request; it exists so the enforcement point is
real and tested, not merely a comment.
"""

from typing import Protocol


class RateLimiter(Protocol):
    """Contract a real limiter must satisfy. `key` identifies the bucket
    to throttle (e.g. `f"login:{normalized_identifier}"` or
    `f"login:{client_ip}"}` — the caller decides, since the exact
    dimension to throttle on is deployment policy, not fixed here).
    """

    def check(self, key: str) -> None:
        """Raise RateLimitExceeded if `key` has exceeded its allowed rate.
        Must be a no-op (never raise) for an implementation with no limit
        configured, so tests and environments without real infrastructure
        keep working."""
        ...


class RateLimitExceeded(Exception):
    """Raised by a RateLimiter.check() implementation; mapped to HTTP 429
    by the API layer."""


class NullRateLimiter:
    """Default: applies no limit. Real throttling (Redis token bucket, a
    reverse-proxy rule, etc.) is a separate, not-yet-built infrastructure
    dependency — see this module's docstring."""

    def check(self, key: str) -> None:
        return None


def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency: swap via `app.dependency_overrides` (or a real
    settings-driven factory once real infrastructure exists) — never
    imported directly by endpoint code, so the swap is total.
    """
    return NullRateLimiter()
