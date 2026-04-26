"""
Unit tests for the Redis-backed rate limiter.

Uses fakeredis so the tests run anywhere without a live Redis instance.
Every test either checks a GTM rule (free = 5 designs/month, pro = 500)
or a behavior we explicitly promised in the module docstring.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.rate_limit import (
    RateLimitDecision,
    RateLimiter,
    _end_of_month,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def redis_client():
    """An in-process fake Redis for unit tests."""
    import fakeredis.aioredis
    r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def limiter(redis_client):
    """RateLimiter bound to a deterministic 'now' = 2026-04-18T10:00:00 UTC."""
    fixed_now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=timezone.utc)
    return RateLimiter(
        redis_client=redis_client,
        now_fn=lambda: fixed_now,
    )


# ─────────────────────────────────────────────────────────────────────
# Tier limits — the GTM contract
# ─────────────────────────────────────────────────────────────────────


def test_free_tier_is_5_designs_per_month(limiter):
    assert limiter.limit_for("free", "design.create") == 5


def test_pro_tier_is_500_designs_per_month(limiter):
    assert limiter.limit_for("pro", "design.create") == 500


def test_team_tier_inherits_pro_limits(limiter):
    assert limiter.limit_for("team", "design.create") == 500


def test_free_tier_ai_optimize_is_5(limiter):
    assert limiter.limit_for("free", "ai.optimize") == 5


def test_pro_tier_ai_optimize_is_100(limiter):
    assert limiter.limit_for("pro", "ai.optimize") == 100


def test_unknown_action_raises(limiter):
    from app.core.rate_limit import RateLimitError
    with pytest.raises(RateLimitError):
        limiter.limit_for("free", "not.an.action")


def test_bulk_generate_never_available_on_free(limiter):
    from app.core.rate_limit import RateLimitError
    with pytest.raises(RateLimitError):
        limiter.limit_for("free", "ai.bulk_generate")


# ─────────────────────────────────────────────────────────────────────
# Atomic counting — core correctness
# ─────────────────────────────────────────────────────────────────────


async def test_first_request_allowed_and_counts_one(limiter):
    uid = uuid4()
    decision = await limiter.check(user_id=uid, plan="free", action="design.create")
    assert decision.allowed is True
    assert decision.current == 1
    assert decision.limit == 5
    assert decision.remaining == 4


async def test_requests_increment_monotonically(limiter):
    uid = uuid4()
    for i in range(1, 6):
        d = await limiter.check(user_id=uid, plan="free", action="design.create")
        assert d.current == i
        assert d.allowed is True


async def test_request_at_limit_still_allowed(limiter):
    """The 5th request on a 5-design plan is the last ALLOWED one — `current == limit`."""
    uid = uuid4()
    for _ in range(4):
        await limiter.check(user_id=uid, plan="free", action="design.create")
    d = await limiter.check(user_id=uid, plan="free", action="design.create")
    assert d.current == 5
    assert d.limit == 5
    assert d.allowed is True
    assert d.remaining == 0


async def test_request_past_limit_is_denied(limiter):
    uid = uuid4()
    for _ in range(5):
        await limiter.check(user_id=uid, plan="free", action="design.create")
    d = await limiter.check(user_id=uid, plan="free", action="design.create")
    assert d.current == 6
    assert d.allowed is False
    assert "limit reached" in d.reason.lower()
    assert "5" in d.reason  # limit number mentioned


# ─────────────────────────────────────────────────────────────────────
# Isolation — users and actions don't bleed into each other
# ─────────────────────────────────────────────────────────────────────


async def test_different_users_have_independent_counters(limiter):
    alice, bob = uuid4(), uuid4()
    for _ in range(5):
        await limiter.check(user_id=alice, plan="free", action="design.create")

    # Alice is at the cap; Bob must still be allowed.
    d_bob = await limiter.check(user_id=bob, plan="free", action="design.create")
    assert d_bob.allowed is True
    assert d_bob.current == 1


async def test_different_actions_have_independent_counters(limiter):
    uid = uuid4()
    for _ in range(5):
        await limiter.check(user_id=uid, plan="free", action="design.create")

    # design.create is at its cap; ai.optimize should still be fresh.
    d = await limiter.check(user_id=uid, plan="free", action="ai.optimize")
    assert d.allowed is True
    assert d.current == 1


async def test_plan_upgrade_reads_higher_limit_but_same_bucket(limiter):
    """Nuance: plan is part of the key; if a user upgrades mid-month, they
    get a fresh counter on the new plan. This is intentional — it prevents
    gaming (downgrade-wait-upgrade) and makes the counter interpretable."""
    uid = uuid4()
    for _ in range(5):
        await limiter.check(user_id=uid, plan="free", action="design.create")

    # User upgrades to pro mid-month.
    d = await limiter.check(user_id=uid, plan="pro", action="design.create")
    assert d.allowed is True
    assert d.current == 1
    assert d.limit == 500


# ─────────────────────────────────────────────────────────────────────
# Monthly reset
# ─────────────────────────────────────────────────────────────────────


async def test_counter_resets_in_new_month(redis_client):
    """April counter and May counter are independent keys."""
    uid = uuid4()

    april = datetime(2026, 4, 18, tzinfo=timezone.utc)
    may = datetime(2026, 5, 1, tzinfo=timezone.utc)

    limiter_april = RateLimiter(redis_client=redis_client, now_fn=lambda: april)
    for _ in range(5):
        await limiter_april.check(user_id=uid, plan="free", action="design.create")

    limiter_may = RateLimiter(redis_client=redis_client, now_fn=lambda: may)
    d = await limiter_may.check(user_id=uid, plan="free", action="design.create")
    assert d.allowed is True
    assert d.current == 1


async def test_resets_at_is_end_of_current_month(limiter):
    uid = uuid4()
    d = await limiter.check(user_id=uid, plan="free", action="design.create")
    # Our fixed clock is 2026-04-18
    assert d.resets_at_utc.year == 2026
    assert d.resets_at_utc.month == 4
    assert d.resets_at_utc.day == 30  # April has 30 days
    assert d.resets_at_utc.hour == 23


def test_end_of_month_february_leap_year():
    """Correctness check — 2024 is a leap year so Feb has 29 days."""
    dt = _end_of_month(datetime(2024, 2, 14, tzinfo=timezone.utc))
    assert dt.day == 29

    dt2 = _end_of_month(datetime(2026, 2, 14, tzinfo=timezone.utc))
    assert dt2.day == 28  # 2026 not a leap year


def test_end_of_month_december():
    dt = _end_of_month(datetime(2026, 12, 15, tzinfo=timezone.utc))
    assert dt.month == 12
    assert dt.day == 31


# ─────────────────────────────────────────────────────────────────────
# Fail-open behavior when Redis is down
# ─────────────────────────────────────────────────────────────────────


async def test_fail_open_when_redis_raises(redis_client, caplog):
    """If Redis raises, the check returns allowed=True with a loud log."""
    class BrokenRedis:
        async def incr(self, key): raise ConnectionError("redis down")
        async def pexpire(self, key, ms): raise ConnectionError("redis down")
        async def get(self, key): raise ConnectionError("redis down")
        async def ttl(self, key): raise ConnectionError("redis down")
        async def delete(self, *keys): raise ConnectionError("redis down")
        async def close(self): pass

    import logging
    caplog.set_level(logging.ERROR)

    limiter = RateLimiter(redis_client=BrokenRedis())
    d = await limiter.check(user_id=uuid4(), plan="free", action="design.create")
    assert d.allowed is True
    assert "unavailable" in d.reason.lower()


# ─────────────────────────────────────────────────────────────────────
# TTL is set only once (on first hit) so the window doesn't reset
# ─────────────────────────────────────────────────────────────────────


async def test_ttl_not_reset_on_subsequent_hits(redis_client, limiter):
    uid = uuid4()
    d1 = await limiter.check(user_id=uid, plan="free", action="design.create")
    key = f"rate:free:{uid}:design.create:202604"
    ttl1 = await redis_client.pttl(key)
    assert ttl1 > 0

    # Sleep a tiny bit and hit again
    import asyncio
    await asyncio.sleep(0.01)

    await limiter.check(user_id=uid, plan="free", action="design.create")
    ttl2 = await redis_client.pttl(key)

    # TTL should have DECREASED (not reset to the full value). Allow
    # floating-point / millisecond wiggle room.
    assert ttl2 <= ttl1, f"TTL was reset: {ttl1} -> {ttl2}"


# ─────────────────────────────────────────────────────────────────────
# current_usage — read-only introspection
# ─────────────────────────────────────────────────────────────────────


async def test_current_usage_is_read_only(limiter):
    uid = uuid4()
    # Not yet used
    u = await limiter.current_usage(user_id=uid, plan="free", action="design.create")
    assert u.current == 0
    assert u.allowed is True

    # Consume 3
    for _ in range(3):
        await limiter.check(user_id=uid, plan="free", action="design.create")

    u = await limiter.current_usage(user_id=uid, plan="free", action="design.create")
    assert u.current == 3

    # current_usage itself must not have incremented
    u2 = await limiter.current_usage(user_id=uid, plan="free", action="design.create")
    assert u2.current == 3


# ─────────────────────────────────────────────────────────────────────
# reset_for_user — admin helper
# ─────────────────────────────────────────────────────────────────────


async def test_reset_for_user_clears_all_actions(limiter):
    uid = uuid4()
    await limiter.check(user_id=uid, plan="free", action="design.create")
    await limiter.check(user_id=uid, plan="free", action="ai.optimize")

    deleted = await limiter.reset_for_user(uid)
    assert deleted >= 2

    # After reset, the counters should be empty.
    u = await limiter.current_usage(user_id=uid, plan="free", action="design.create")
    assert u.current == 0


async def test_reset_for_user_single_action(limiter):
    uid = uuid4()
    await limiter.check(user_id=uid, plan="free", action="design.create")
    await limiter.check(user_id=uid, plan="free", action="ai.optimize")

    await limiter.reset_for_user(uid, action="design.create")

    # design.create cleared; ai.optimize preserved
    u_create = await limiter.current_usage(user_id=uid, plan="free", action="design.create")
    u_opt = await limiter.current_usage(user_id=uid, plan="free", action="ai.optimize")
    assert u_create.current == 0
    assert u_opt.current == 1


# ─────────────────────────────────────────────────────────────────────
# Decision object sanity
# ─────────────────────────────────────────────────────────────────────


async def test_decision_remaining_never_negative(limiter):
    uid = uuid4()
    # Blow past the cap
    for _ in range(8):
        d = await limiter.check(user_id=uid, plan="free", action="design.create")
    assert d.remaining == 0
    assert d.current > d.limit


async def test_decision_is_frozen_dataclass(limiter):
    """RateLimitDecision is frozen — it cannot be mutated by callers."""
    uid = uuid4()
    d = await limiter.check(user_id=uid, plan="free", action="design.create")
    with pytest.raises((AttributeError, TypeError, Exception)):
        d.allowed = False  # type: ignore[misc]
