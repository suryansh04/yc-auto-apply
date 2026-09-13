"""API tests run against the same in-memory FakeRepos the application
tests use — no Postgres, no network, no env vars (global constraint)."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture
def client(repos):
    app = create_app(repos_factory=lambda: repos)
    with TestClient(app) as c:
        yield c
