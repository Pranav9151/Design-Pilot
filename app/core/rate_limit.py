"""
Per-user, per-action rate limiter.

**Purpose:**
    Enforce the GTM free-tier cap (5 designs / month) and the Pro-tier
    fair-use ceiling (500 designs / month) at the application layer.
    Also protects expensive endpoints (AI optimize, quote requests) from
    abuse and unbounded Anthropic/Xometry cost.

**Strategy (month-bucketed atomic counters):**
    Key shape:   rate:{plan}:{user_id}:{action}:{YYYYMM}
    Op:          INCR key; if == 1, set PEXPIRE to end-of-month + 1 day
    Decision:    counter > limit  →  reject with current / limit / resets_at

    Month-bucketed keys are simple, accurate (no "leaky bucket" rounding),
    auto-expire without cleanup jobs, and cheap (one INCR + one EXPIRE
    on the first call of each month).

**Failure mode (if Redis is down):**
    We **fail open**: a rate-limit check error logs an alert and allows
    the request. Rationale: a service outage is worse than temporarily
    not enforcing fair-use caps. The alternative ("fail closed") means
    Redis downtime = total outage. Failing open requires us to treat
    limit breaches detected after-the-fact via billing / audit, which
    we already do.

**Limits live in settings** (app.core.config) so they can be tuned
    without code changes.

**Usage:**
    rate_limiter = RateLimiter(...)
    decision = await rate_limiter.check(
        user_id=current_user.id,
        plan=current_user.plan,          # "free" | "pro"
        action="design.create",
    )
    if not decision.allowed:
        raise HTTPException(429, decision.reason)
"""
from __future__ import annotations

import calendar
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

import structlog

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Types
# ═════════════════════════════════════════════════════════════════════


Plan = Literal["free", "pro", "team", "enterprise"]
Action = Literal[
    "design.create",
    "ai.optimize",
    "quote.send",
    "ai.design_review",
    "ai.bulk_generate",
]


class AsyncRedisLike(Protocol):
    """Minimal protocol — lets us swap real redis for fakeredis in tests."""

    async def incr(self, key: str) -> int: ...
    async def pexpire(self, key: str, ms: int) -> int: ...
    async def get(self, key: str) -> bytes | None: ...
    async def ttl(self, key: str) -> int: ...
    async def delete(self, *keys: str) -> int: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a rate-limit check.

    `allowed=True` means the request may proceed; the caller should still
    surface `current` / `limit` / `resets_at_utc` as X-RateLimit-* headers.
    """

    allowed: bool
    plan: str
    action: str
    current: int            # count INCLUDING this request
    limit: int
    resets_at_utc: datetime
    reason: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.current)


# ═════════════════════════════════════════════════════════════════════
# Core
# ═════════════════════════════════════════════════════════════════════


class RateLimitError(Exception):
    """Only raised for misconfiguration (unknown action etc.), not for
    "limit exceeded" — that's conveyed via RateLimitDecision.allowed=False."""


class RateLimiter:
    """Per-user, per-action monthly rate limiter backed by Redis."""

    def __init__(
        self,
        redis_client: AsyncRedisLike,
        settings: Settings | None = None,
        now_fn=None,
    ) -> None:
        self.redis = redis_client
        self.settings = settings or get_settings()
        # Injected now-function for deterministic testing.
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # ── Public API ────────────────────────────────────────────────

    def limit_for(self, plan: str, action: str) -> int:
        """Look up the numeric limit for (plan, action). Raises for unknown action."""
        # Design creation
        if action == "design.create":
            if plan == "free":
                return self.settings.RATE_LIMIT_FREE_DESIGNS_PER_MONTH
            if plan in ("pro", "team", "enterprise"):
                return self.settings.RATE_LIMIT_PRO_DESIGNS_PER_MONTH
            raise RateLimitError(f"unknown plan: {plan!r}")

        # AI optimize
        if action == "ai.optimize":
            return 5 if plan == "free" else 100

        # Quote send (Xometry/Protolabs email)
        if action == "quote.send":
            return 3 if plan == "free" else 50

        # AI design review
        if action == "ai.design_review":
            return 2 if plan == "free" else 50

        # AI bulk generate (admin-gated, never free)
        if action == "ai.bulk_generate":
            if plan == "free":
                raise RateLimitError("ai.bulk_generate not available on free plan")
            return 20

        raise RateLimitError(f"unknown action: {action!r}")

    async def check(
        self,
        *,
        user_id: UUID | str,
        plan: str,
        action: str,
    ) -> RateLimitDecision:
        """Increment the counter for (user, action) and decide.

        This method is atomic per-request: even under concurrency Redis INCR
        guarantees no two requests see the same pre-increment value. The
        limit check after INCR is therefore exact.
        """
        try:
            limit = self.limit_for(plan, action)
        except RateLimitError as exc:
            # Unknown plan/action combination is treated as "not allowed" —
            # the caller's route definition is wrong.
            logger.error("rate_limit_misconfig", plan=plan, action=action, error=str(exc))
            return RateLimitDecision(
                allowed=False,
                plan=plan,
                action=action,
                current=0,
                limit=0,
                resets_at_utc=_end_of_month(self._now()),
                reason=str(exc),
            )

        now = self._now()
        bucket = now.strftime("%Y%m")
        key = f"rate:{plan}:{user_id}:{action}:{bucket}"

        try:
            current = await self.redis.incr(key)
            # Only set expiry on the first hit this month; PEXPIRE on an
            # existing counter would reset the TTL, breaking monthly reset.
            if current == 1:
                end = _end_of_month(now)
                ms_until_end = int((end - now).total_seconds() * 1000) + 24 * 3600 * 1000
                await self.redis.pexpire(key, ms_until_end)
        except Exception as exc:
            # FAIL OPEN — see module docstring.
            logger.error(
                "rate_limit_redis_unreachable",
                user_id=str(user_id),
                action=action,
                error=str(exc),
                failing_open=True,
            )
            return RateLimitDecision(
                allowed=True,
                plan=plan,
                action=action,
                current=0,
                limit=limit,
                resets_at_utc=_end_of_month(now),
                reason="rate-limit backend unavailable; request allowed",
            )

        resets_at = _end_of_month(now)

        if current > limit:
            logger.info(
                "rate_limited",
                user_id=str(user_id),
                plan=plan,
                action=action,
                current=current,
                limit=limit,
            )
            return RateLimitDecision(
                allowed=False,
                plan=plan,
                action=action,
                current=current,
                limit=limit,
                resets_at_utc=resets_at,
                reason=(
                    f"{action} limit reached for {plan} plan "
                    f"({current-1}/{limit} used this month)"
                ),
            )

        return RateLimitDecision(
            allowed=True,
            plan=plan,
            action=action,
            current=current,
            limit=limit,
            resets_at_utc=resets_at,
        )

    async def current_usage(
        self,
        *,
        user_id: UUID | str,
        plan: str,
        action: str,
    ) -> RateLimitDecision:
        """Read-only current state — does NOT increment. For dashboards."""
        try:
            limit = self.limit_for(plan, action)
        except RateLimitError as exc:
            return RateLimitDecision(
                allowed=False, plan=plan, action=action, current=0, limit=0,
                resets_at_utc=_end_of_month(self._now()), reason=str(exc),
            )

        now = self._now()
        bucket = now.strftime("%Y%m")
        key = f"rate:{plan}:{user_id}:{action}:{bucket}"

        raw = await self.redis.get(key)
        current = int(raw) if raw else 0

        return RateLimitDecision(
            allowed=current < limit,
            plan=plan,
            action=action,
            current=current,
            limit=limit,
            resets_at_utc=_end_of_month(now),
        )

    async def reset_for_user(self, user_id: UUID | str, action: str | None = None) -> int:
        """Admin/test helper: drop the counter for a user (all actions or just one).

        In production this is guarded by `system.audit.view` + `system.settings.edit`
        permissions; do not wire this to an end-user endpoint.
        """
        now = self._now()
        bucket = now.strftime("%Y%m")
        deleted = 0

        if action:
            key = f"rate:*:{user_id}:{action}:{bucket}"
            # For simplicity in v1 we only know the plan if the caller supplies
            # it — deletion by pattern is a Redis-cluster hazard. We'll just
            # delete the 4 plan variants we support.
            for plan in ("free", "pro", "team", "enterprise"):
                k = f"rate:{plan}:{user_id}:{action}:{bucket}"
                deleted += await self.redis.delete(k)
        else:
            for plan in ("free", "pro", "team", "enterprise"):
                for act in ("design.create", "ai.optimize", "quote.send",
                            "ai.design_review", "ai.bulk_generate"):
                    k = f"rate:{plan}:{user_id}:{act}:{bucket}"
                    deleted += await self.redis.delete(k)

        return deleted


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _end_of_month(now: datetime) -> datetime:
    """Return the final microsecond of `now`'s month, in UTC."""
    last_day = calendar.monthrange(now.year, now.month)[1]
    return datetime(
        now.year, now.month, last_day,
        23, 59, 59, 999_999,
        tzinfo=timezone.utc,
    )
