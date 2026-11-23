import pytest


@pytest.fixture(autouse=True)
def sec_user_agent(monkeypatch):
    """Every test gets a dummy SEC_USER_AGENT, so tests never need (or read) the real .env value."""
    monkeypatch.setenv("SEC_USER_AGENT", "test-agent test@example.com")
