"""Fix round 1 (review item #2): `app.api.jobs._draft`'s defensive
`try/except RuntimeError` around `build_critic` (T13-2's ruling) had zero
test coverage — deleting the whole block and replacing it with a bare
`critic = build_critic(settings)` left the entire 878-test suite green.

This exercises `_draft` directly (no HTTP, no real DB): every repo class
and `build_drafter`/`build_critic`/`on_gemini_free_tier` it imports locally
are patched to no-op stand-ins, `DraftOutreach` is patched to a recorder,
and `build_critic` is made to raise — mirroring
`tests/test_critic_wiring.py::test_draft_command_wires_a_critic_into_draftoutreach`'s
pattern for the CLI's own `draft` command.
"""
from types import SimpleNamespace

from app.config import get_settings


class _StubSession:
    def commit(self):
        pass


def test_draft_stage_falls_back_to_no_critic_when_build_critic_fails(monkeypatch, caplog):
    import app.api.deps as deps
    from app.adapters.db import repositories

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GEMINI_TIER", "paid")
    get_settings.cache_clear()

    captured = {}

    class _RecordingDraftOutreach:
        def __init__(self, drafter, repos, keep_unverified=False, critic=None,
                     subject_writer=None):
            captured["drafter"] = drafter
            captured["critic"] = critic
            captured["subject_writer"] = subject_writer

        def run(self, limit=None):
            return SimpleNamespace(drafted=0, manual_write=0, retried=0, errors=0, aborted=False)

    drafter_sentinel = object()

    def _raising_build_critic(settings):
        raise RuntimeError("SARVAM_API_KEY is not set")

    monkeypatch.setattr(deps, "build_drafter", lambda settings: drafter_sentinel)
    monkeypatch.setattr(deps, "build_critic", _raising_build_critic)
    monkeypatch.setattr(deps, "on_gemini_free_tier", lambda settings: False)
    monkeypatch.setattr(deps, "build_subject_writer", lambda settings: None)

    for repo_name in (
        "CompanyLaunchRepo", "CompanyNewsRepo", "CompanyRepo", "FounderRepo",
        "JobPostingRepo", "MessageRepo", "OutreachRepo", "ResumeProfileRepo",
    ):
        monkeypatch.setattr(repositories, repo_name, lambda session: object())

    monkeypatch.setattr(
        "app.application.draft_outreach.DraftOutreach", _RecordingDraftOutreach
    )

    from app.api import jobs

    import logging

    with caplog.at_level(logging.WARNING):
        summary = jobs._draft(_StubSession(), lambda done, total=None: None)

    # The stage still runs to completion — a critic that can't even be
    # BUILT must not abort a drafting run any more than one that raises at
    # call time may (D22's "never blocks a draft", extended to
    # construction).
    assert captured.get("drafter") is drafter_sentinel
    assert captured.get("critic") is None
    assert "drafted 0" in summary
    assert any("voice critic" in r.message.lower() for r in caplog.records)
    # And the RuntimeError's own text (just an env var name here, but the
    # same log-safety discipline as `run_critic`) is what gets logged —
    # never swallowed silently.
    assert "SARVAM_API_KEY" in caplog.text


def test_draft_stage_passes_the_built_critic_through_on_success(monkeypatch):
    """The flip side of the guard above: when `build_critic` succeeds,
    `_draft` must actually hand that critic to `DraftOutreach` — not just
    swallow failures gracefully."""
    import app.api.deps as deps
    from app.adapters.db import repositories

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GEMINI_TIER", "paid")
    get_settings.cache_clear()

    captured = {}

    class _RecordingDraftOutreach:
        def __init__(self, drafter, repos, keep_unverified=False, critic=None,
                     subject_writer=None):
            captured["critic"] = critic

        def run(self, limit=None):
            return SimpleNamespace(drafted=0, manual_write=0, retried=0, errors=0, aborted=False)

    critic_sentinel = object()
    monkeypatch.setattr(deps, "build_drafter", lambda settings: object())
    monkeypatch.setattr(deps, "build_critic", lambda settings: critic_sentinel)
    monkeypatch.setattr(deps, "on_gemini_free_tier", lambda settings: False)
    monkeypatch.setattr(deps, "build_subject_writer", lambda settings: None)

    for repo_name in (
        "CompanyLaunchRepo", "CompanyNewsRepo", "CompanyRepo", "FounderRepo",
        "JobPostingRepo", "MessageRepo", "OutreachRepo", "ResumeProfileRepo",
    ):
        monkeypatch.setattr(repositories, repo_name, lambda session: object())

    monkeypatch.setattr(
        "app.application.draft_outreach.DraftOutreach", _RecordingDraftOutreach
    )

    from app.api import jobs

    jobs._draft(_StubSession(), lambda done, total=None: None)

    assert captured.get("critic") is critic_sentinel
