"""Session-lifecycle tests for the production `real_repos` dependency
(app.api.deps) — the seam every route falls back to when not overridden by
a `repos_factory` in tests. `tests/api/test_stats.py` and friends all run
through `FakeRepos`, which has no session to leak; these tests exercise
`real_repos` itself against a fake session (no DB, no network) so the
close-on-every-path behaviour is test-verified, not just inspected — the
same bug class ("stale idle-in-transaction connection") that has already
blocked a migration on this project (T3 fix round 2, point 2)."""

import pytest

from app.api.deps import Repos, real_repos


class _FakeDBSession:
    def __init__(self):
        self.closed = False
        self.commit_calls = 0

    def commit(self):
        self.commit_calls += 1

    def close(self):
        self.closed = True


def test_real_repos_closes_the_session_on_the_success_path():
    session = _FakeDBSession()
    gen = real_repos(session_factory=lambda: session)

    repos = next(gen)
    assert isinstance(repos, Repos)
    assert repos.session is session
    assert not session.closed

    with pytest.raises(StopIteration):
        next(gen)
    assert session.closed


def test_real_repos_closes_the_session_when_the_route_raises():
    session = _FakeDBSession()
    gen = real_repos(session_factory=lambda: session)
    next(gen)
    assert not session.closed

    # Mirrors what FastAPI does when a route raises while this dependency
    # is active: it throws the exception into the generator at the `yield`.
    with pytest.raises(ValueError):
        gen.throw(ValueError("route blew up"))
    assert session.closed


def test_real_repos_never_commits():
    """Point 3: `get_repos`/`real_repos` must not commit - a write route
    commits explicitly on its own action instead."""
    session = _FakeDBSession()
    gen = real_repos(session_factory=lambda: session)
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)
    assert session.commit_calls == 0
