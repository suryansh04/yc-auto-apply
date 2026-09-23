"""`POST /api/outreach/{id}/suggest` - the rewrite behind each highlight.

Operator request (2026-09-22): "when i hover over it, it shows suggestion
just like grammarly use sarvam for suggestion".

This route is advice on top of advice, and the tests below are mostly
about what it does when things go wrong: the checks are already on the
screen when it is called, and nothing here may take them off it.
"""


def _inject(repos):
    """A reviser that records the context it was handed. (`fake_reviser`
    is a fixture; these two tests want one mid-test, after the body has
    been replaced.)"""

    class Recording:
        def __init__(self):
            self.context = None

        def revise(self, context):
            self.context = context
            return []

    repos.reviser = Recording()
    return repos.reviser


def _suggest(client, draft, **payload):
    return client.post(f"/api/outreach/{draft.outreach.id}/suggest", json=payload or {})


def test_a_rewrite_comes_back_with_the_words_it_replaces(client, flagged_draft, fake_reviser):
    # Riya's draft is the flagged one. Its notes are not all about words
    # - "fewer than 2 paragraphs" points at no phrase at all - so the
    # index matters: it is a position in `/check`'s highlight list, not a
    # position in "the notes that happened to have a quote".
    fake_reviser.answer = [{"index": 1, "replacement": "Quick one.", "why": "nobody writes that"}]
    body = _suggest(client, flagged_draft).json()

    assert body["unavailable"] is None
    [suggestion] = body["suggestions"]
    assert suggestion["replacement"] == "Quick one."
    assert suggestion["why"] == "nobody writes that"
    # The quote and the note travel with it, so a hover card needs nothing
    # else to render, and `index` still points into `/check`'s highlights.
    assert "I hope this email finds you well" in suggestion["quote"]
    assert suggestion["message"]  # the note this rewrite answers, verbatim
    assert suggestion["index"] == 1


def test_the_model_is_asked_about_the_text_being_typed_not_the_stored_one(
    client, flagged_draft, fake_reviser
):
    typed = "I hope this email finds you well. I am writing to you today."
    _suggest(client, flagged_draft, body=typed, subject="hello")
    assert fake_reviser.context["body"] == typed


def test_a_clean_draft_asks_nothing_and_answers_nothing(client, pending_draft, fake_reviser):
    body = _suggest(client, pending_draft).json()
    assert body["suggestions"] == []
    assert fake_reviser.context is None


def test_a_reviser_that_fails_still_answers_200(client, flagged_draft, fake_reviser):
    class Boom:
        def revise(self, context):
            raise RuntimeError("provider detail that must not surface")

    fake_reviser.revise = Boom().revise
    response = _suggest(client, flagged_draft)
    assert response.status_code == 200
    assert response.json()["suggestions"] == []


def test_an_unconfigured_provider_names_the_setting_not_the_key(
    client, repos, flagged_draft, monkeypatch
):
    # No reviser injected at all: the route tries to build a real one and
    # is told what is missing. The answer is still a 200 with the checks
    # untouched - and it names a SETTING, never a value.
    repos.reviser = None
    monkeypatch.setattr("app.api.routers.queue.build_reviser",
                        lambda settings: (_ for _ in ()).throw(RuntimeError("SARVAM_API_KEY is not set")))
    monkeypatch.setattr("app.api.routers.queue.get_settings", lambda: None)
    body = _suggest(client, flagged_draft).json()
    assert body["unavailable"] == "SARVAM_API_KEY is not set"
    assert body["suggestions"] == []


def test_no_such_outreach_is_404(client):
    assert client.post("/api/outreach/9999/suggest", json={}).status_code == 404


def test_suggesting_writes_nothing(client, repos, flagged_draft, fake_reviser):
    fake_reviser.answer = [{"index": 1, "replacement": "Different words.", "why": "x"}]
    before = client.get(f"/api/queue/{flagged_draft.outreach.id}").json()["body"]
    _suggest(client, flagged_draft)
    assert client.get(f"/api/queue/{flagged_draft.outreach.id}").json()["body"] == before


def test_the_company_paragraph_is_always_offered_a_rewrite(client, repos, pending_draft):
    """Operator request (2026-09-23): an AI suggestion and the generic
    line, side by side, on the one paragraph the letter researches. So it
    is sent to the reviser whether or not a check complained about it."""
    from app.domain import letter

    insight = "You did not build another job portal. You built a voice layer."
    row = repos.message.get_outbound(pending_draft.outreach.id, 0)
    repos.message.upsert(id=row.id, body=letter.assemble_body("Vahan", [insight]))

    fake = _inject(repos)
    _suggest(client, pending_draft)
    quotes = [s["quote"] for s in fake.context["spans"]]
    assert insight in quotes


def test_the_company_paragraph_is_sent_last_so_its_index_is_predictable(
    client, repos, flagged_draft
):
    """The review screen appends the same paragraph's mark after the
    checks' marks, so both sides give it the same index without either
    matching on text."""
    from app.domain import letter

    insight = "You did not build another job portal. You built a voice layer."
    row = repos.message.get_outbound(flagged_draft.outreach.id, 0)
    repos.message.upsert(id=row.id, body=letter.assemble_body("Vahan", [insight]))

    fake = _inject(repos)
    _suggest(client, flagged_draft)
    spans = fake.context["spans"]
    assert spans[-1]["quote"] == insight
    assert spans[-1]["index"] == max(s["index"] for s in spans)
