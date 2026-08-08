"""Global pytest configuration and fixtures for Freja test suite."""

import pytest
from backend.middleware.auth import reset_auth_lockout


@pytest.fixture(autouse=True)
def auto_reset_auth_lockout():
    """Automatically resets the in-memory rate-limiter and lockout state before and
    after every test so tests running against TestClient don't accumulate failed
    attempts against the shared 'testclient' host string."""
    reset_auth_lockout()
    yield
    reset_auth_lockout()
