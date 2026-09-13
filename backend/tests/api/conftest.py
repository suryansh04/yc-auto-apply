"""API tests run against the same in-memory FakeRepos the application
tests use — no Postgres, no network, no env vars (global constraint)."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from tests.conftest import default_test_job_factories


@pytest.fixture
def client(repos):
    # Task 6: the app's startup hook recovers stale jobs through the same
    # job factories a route would use - default them to the offline
    # `repos.job` here too, so every API test stays DB-free even if it
    # never touches `/api/jobs` itself. Job-specific fixtures
    # (`job_probe`, `blocking_job`, `failing_job`) swap these again on
    # `client.app.state` after this fixture hands back the client.
    session_factory, runner_factory = default_test_job_factories(repos)
    app = create_app(
        repos_factory=lambda: repos,
        job_session_factory=session_factory,
        job_runner_factory=runner_factory,
    )
    with TestClient(app) as c:
        yield c
