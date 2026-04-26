"""
Integration-test-specific fixtures.

This conftest requests `apply_migrations` as a session-scoped autouse
fixture so every integration test gets a fully-migrated DB without having
to declare the dependency explicitly.  Unit/security/engineering tests in
tests/unit/, tests/security/, tests/engineering/ never touch this file, so
they run without a DB connection.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _integration_db_setup(apply_migrations):  # noqa: PT004
    """Auto-apply migrations for every test in the integration suite."""
    yield
