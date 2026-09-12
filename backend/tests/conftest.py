"""Root fixtures (Task 16).

The in-memory `FakeRepos` bundle and the per-task founder fixtures are
defined in `tests/application/conftest.py` (ruling: fixtures live in
conftest, backed by in-memory fakes, and later tasks reuse them). The
review-loop CLI tests and the offline end-to-end test sit at the `tests/`
root, so the ones they need are re-exported here - pytest registers a
fixture wherever its function object appears in a conftest namespace, and
for tests under `tests/application/` the nearer conftest still wins (it
is the same object either way).

The brief's `test_cli_review.py` names `a_pending_message` /
`a_news_source`; they are built from the same fake row classes, so the
review screen is rendered from exactly the shapes the use case hands the
CLI.
"""

import pytest

from tests.application.conftest import (  # noqa: F401 - fixture re-exports
    PENDING_BODY,
    PENDING_SUBJECT,
    VAHAN_NEWS,
    FakeMessageRow,
    FakeNewsRow,
    manual_write_pending,
    no_material_pending,
    one_approved,
    one_pending,
    repos,
    two_pending,
)


@pytest.fixture
def a_news_source() -> FakeNewsRow:
    return FakeNewsRow(id=1, company_id=5, title=VAHAN_NEWS, source="The Economic Times")


@pytest.fixture
def a_pending_message() -> FakeMessageRow:
    return FakeMessageRow(
        id=1, outreach_id=5, sequence_step=0, direction="outbound",
        subject=PENDING_SUBJECT, body=PENDING_BODY,
        research_source_type="news", research_source_id=1,
    )
