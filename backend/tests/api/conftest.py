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


class _OfflineReviser:
    """Stands in for a real `Reviser` so no API test can reach a provider.

    `POST /api/outreach/{id}/suggest` builds a real Sarvam/Gemini adapter
    when `Repos.reviser` is None, and `backend/.env` is read by
    `get_settings()` even under `env -i` - so on the operator's own
    machine an un-injected test with a flagged draft would make a live,
    billed call. The autouse fixture below fills the seam for every API
    test; `fake_reviser` replaces it where a test actually wants
    suggestions back. Same discipline as `fake_sender` and for the same
    reason: the offline suite must be structurally incapable of touching
    the network, not merely careful about it."""

    calls = 0

    def revise(self, context):
        type(self).calls += 1
        raise AssertionError("an offline test reached the reviser seam")


@pytest.fixture(autouse=True)
def _no_real_reviser(repos):
    repos.reviser = _OfflineReviser()
    return repos.reviser


@pytest.fixture
def fake_reviser(repos):
    """A `Reviser` that answers with whatever the test sets on
    `.answer`, and records the context it was handed."""

    class FakeReviser:
        def __init__(self):
            self.answer = []
            self.context = None

        def revise(self, context):
            self.context = context
            return self.answer

    reviser = FakeReviser()
    repos.reviser = reviser
    return reviser


class _OfflineParser:
    """Stands in for a real `ResumeParser` so no API test can reach a
    provider, for the same reason `_OfflineReviser` exists above:
    `POST /api/resume/parse` builds a real adapter when `Repos.parser` is
    None, and `backend/.env` is read even under `env -i`."""

    def parse(self, raw_text):
        raise AssertionError("an offline test reached the resume-parser seam")


@pytest.fixture(autouse=True)
def _no_real_parser(repos):
    repos.parser = _OfflineParser()
    return repos.parser


class _OfflineInsightWriter:
    """Stands in for a real `InsightWriter`, for the same reason
    `_OfflineReviser` and `_OfflineParser` exist: `POST
    /api/outreach/{id}/insight` builds a real adapter when
    `Repos.insight_writer` is None, and `backend/.env` is read even under
    `env -i`."""

    def write(self, context):
        raise AssertionError("an offline test reached the insight-writer seam")


@pytest.fixture(autouse=True)
def _no_real_insight_writer(repos):
    repos.insight_writer = _OfflineInsightWriter()
    return repos.insight_writer


@pytest.fixture
def fake_insight_writer(repos):
    """An `InsightWriter` that answers with whatever the test sets on
    `.answer`, and records the context it was handed."""

    class FakeInsightWriter:
        def __init__(self):
            self.answer = {"paragraph": "", "why": ""}
            self.context = None

        def write(self, context):
            self.context = context
            return self.answer

    writer = FakeInsightWriter()
    repos.insight_writer = writer
    return writer


@pytest.fixture
def fake_parser(repos):
    """A `ResumeParser` that answers with whatever the test sets on
    `.claims`, and records the text it was handed."""

    class FakeParser:
        def __init__(self):
            self.claims = []
            self.text = None

        def parse(self, raw_text):
            self.text = raw_text
            return self.claims

    parser = FakeParser()
    repos.parser = parser
    return parser
