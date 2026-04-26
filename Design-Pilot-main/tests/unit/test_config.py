"""Unit tests for app.core.config — loads env, has sane defaults."""
from __future__ import annotations

from app.core.config import Settings, get_settings


def test_settings_cached_singleton():
    """get_settings is memoized — same instance every call."""
    a = get_settings()
    b = get_settings()
    assert a is b


def test_settings_loads_env():
    """Settings picks up APP_NAME / DATABASE_URL from env."""
    s = Settings()
    assert s.APP_NAME == "DesignPilot MECH"
    assert "designpilot" in s.DATABASE_URL


def test_settings_cors_origins_list():
    """cors_origins_list splits the comma-separated string."""
    s = Settings(CORS_ORIGINS="http://a,http://b , http://c")
    assert s.cors_origins_list == ["http://a", "http://b", "http://c"]


def test_settings_is_production_flag():
    """is_production derives from APP_ENV."""
    assert Settings(APP_ENV="production").is_production is True
    assert Settings(APP_ENV="development").is_production is False
    assert Settings(APP_ENV="staging").is_production is False


def test_feature_flags_default_false():
    """v1.0 features that belong to v1.5+ are disabled by default."""
    s = Settings()
    assert s.FEATURE_TEAMS is False
    assert s.FEATURE_MOBILE is False
    assert s.FEATURE_FEA is False
    assert s.FEATURE_2D_DRAWINGS is False


def test_rate_limits_v1_defaults():
    """Free tier is 5 designs/month per the GTM pricing."""
    s = Settings()
    assert s.RATE_LIMIT_FREE_DESIGNS_PER_MONTH == 5
    assert s.RATE_LIMIT_PRO_DESIGNS_PER_MONTH == 500
