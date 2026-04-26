"""
Unit tests for the audit service's `_redact` helper.

The redactor protects us from ever logging sensitive fields, even if a
route handler accidentally passes them through in metadata.
"""
from __future__ import annotations

from app.audit.service import _redact


def test_redact_top_level_sensitive_keys():
    out = _redact({"email": "a@b.com", "password": "hunter2"})
    assert out == {"email": "a@b.com", "password": "<redacted>"}


def test_redact_nested():
    out = _redact({"user": {"name": "a", "api_key": "sk-xxx"}})
    assert out == {"user": {"name": "a", "api_key": "<redacted>"}}


def test_redact_case_insensitive():
    """Sensitive key matching is case-insensitive."""
    out = _redact({"Password": "x", "API_KEY": "y", "Cookie": "z"})
    assert out["Password"] == "<redacted>"
    assert out["API_KEY"] == "<redacted>"
    assert out["Cookie"] == "<redacted>"


def test_redact_inside_lists():
    out = _redact({"items": [{"token": "t"}, {"name": "n"}]})
    assert out == {"items": [{"token": "<redacted>"}, {"name": "n"}]}


def test_redact_depth_limited():
    """Guard against pathological deep nesting."""
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"token": "x"}}}}}}}}
    # Shouldn't raise; returns a depth-limited marker on the deepest level
    out = _redact(deep)
    assert isinstance(out, dict)


def test_redact_preserves_non_sensitive_scalars():
    assert _redact(42) == 42
    assert _redact("hello") == "hello"
    assert _redact(None) is None
    assert _redact([1, 2, 3]) == [1, 2, 3]
