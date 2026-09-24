"""`POST /api/outreach/{id}/insight` - the company paragraph, written
from the company's own YC description.

Operator request (2026-09-23): "when i am clicking ai suggestion there is
no ai suggestion coming ... extract the product description of the
particular yc company, pass it to sarvam and make a similar line". The
**AI suggestion** tab was asking the `Reviser`, which only ever rewrites
a span a mechanical check flagged - and the company paragraph is usually
fine as English, so the honest answer was "nothing sharper to offer".

Advice, like `/suggest`: this route writes nothing and refuses nothing.
Every way of having nothing to offer answers 200 with a null paragraph
and a line saying why, because the letter is already on screen and must
stay there.
"""

GOOD = (
    "I like how Vahan puts the job and the worker in the same phone call. Hiring for these "
    "roles usually runs through agents who take a cut and lose the candidate halfway, so the "
    "person who needs the work waits longest. You made the slow part the part a machine can "
    "do, and that is worth building."
)


def test_it_writes_a_paragraph_from_what_the_founders_wrote(
    client, repos, pending_draft, fake_insight_writer
):
    fake_insight_writer.answer = {"paragraph": GOOD, "why": "their own framing of the problem"}
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight").json()

    assert body["paragraph"] == GOOD
    assert body["why"] == "their own framing of the problem"
    assert body["unavailable"] is None


def test_the_model_is_handed_the_company_description(
    client, repos, pending_draft, fake_insight_writer
):
    """The whole point of the request: the source is the text the
    founders wrote for their own YC page, not the letter."""
    fake_insight_writer.answer = {"paragraph": GOOD, "why": ""}
    client.post(f"/api/outreach/{pending_draft.outreach.id}/insight")

    context = fake_insight_writer.context
    assert context["company_name"] == "Vahan"
    assert context["one_liner"] == "Recruitment over voice"
    assert len(context["description"]) > 80


def test_it_writes_nothing_to_the_draft(client, repos, pending_draft, fake_insight_writer):
    """An offer, not an edit. The operator presses Use this."""
    before = client.get(f"/api/queue/{pending_draft.outreach.id}").json()["body"]
    fake_insight_writer.answer = {"paragraph": GOOD, "why": ""}
    client.post(f"/api/outreach/{pending_draft.outreach.id}/insight")
    assert client.get(f"/api/queue/{pending_draft.outreach.id}").json()["body"] == before


def test_no_yc_description_falls_back_to_their_news_and_launches(
    client, repos, pending_draft, fake_insight_writer
):
    """Live finding (2026-09-24): Inito has no YC description but does
    have a news story, and its paragraph could not be rewritten."""
    repos.company.rows[pending_draft.outreach.id].long_description = None
    fake_insight_writer.answer = {"paragraph": GOOD, "why": ""}
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight").json()

    assert body["paragraph"] == GOOD
    material = fake_insight_writer.context["description"]
    assert material.startswith("A news headline about them: ")


def test_a_company_with_nothing_at_all_says_so_rather_than_inventing_one(
    client, repos, pending_draft, fake_insight_writer
):
    company_id = pending_draft.outreach.id
    repos.company.rows[company_id].long_description = None
    repos.news.rows = [n for n in repos.news.rows if n.company_id != company_id]
    repos.launch.rows = [l for l in repos.launch.rows if l.company_id != company_id]
    body = client.post(f"/api/outreach/{company_id}/insight").json()

    assert body["paragraph"] is None
    assert "nothing about this company" in body["unavailable"]
    # And nothing was asked of the model.
    assert fake_insight_writer.context is None


def test_an_answer_that_quotes_their_metrics_is_dropped(
    client, repos, pending_draft, fake_insight_writer
):
    """Every number in a YC description is a fundraising fact. Reciting
    one back at a founder is the opposite of this paragraph's job."""
    fake_insight_writer.answer = {
        "paragraph": GOOD.replace("halfway", "in 40% of cases"), "why": "",
    }
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight").json()
    assert body["paragraph"] is None
    assert body["unavailable"]


def test_an_empty_answer_is_not_an_error(client, repos, pending_draft, fake_insight_writer):
    """The prompt asks for an empty paragraph when the description is too
    thin to say anything true - a generic line already exists for that
    case, and inventing a detail is worse than having nothing."""
    fake_insight_writer.answer = {"paragraph": "  ", "why": ""}
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight")
    assert r.status_code == 200
    assert r.json()["paragraph"] is None


def test_a_provider_failure_never_leaks_its_response(
    client, repos, pending_draft
):
    class Boom:
        def write(self, context):
            raise RuntimeError("401 from provider: key sk_live_secret is bad")

    repos.insight_writer = Boom()
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight")
    assert r.status_code == 200
    assert "sk_live_secret" not in r.text
    assert r.json()["paragraph"] is None


def test_a_missing_key_names_the_setting_not_its_value(
    client, repos, pending_draft, monkeypatch
):
    repos.insight_writer = None
    monkeypatch.setattr(
        "app.api.routers.queue.build_insight_writer",
        lambda settings: (_ for _ in ()).throw(RuntimeError("SARVAM_API_KEY is not set")),
    )
    monkeypatch.setattr("app.api.routers.queue.get_settings", lambda: None)
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight").json()
    assert body["unavailable"] == "SARVAM_API_KEY is not set"
    assert body["paragraph"] is None


def test_an_unknown_outreach_is_404(client, repos):
    assert client.post("/api/outreach/9999/insight").status_code == 404


def test_another_asks_for_something_other_than_what_is_on_screen(
    client, repos, pending_draft, fake_insight_writer
):
    """Live finding (2026-09-23): "it is showing the same for orangewood
    and zingbus". Without naming what has already been offered, "write me
    another" and "write me one" are the same request."""
    from app.domain import letter

    row = repos.message.get_outbound(pending_draft.outreach.id, 0)
    in_the_letter = "You built a voice layer for people who do not type."
    repos.message.upsert(id=row.id, body=letter.assemble_body("Vahan", [in_the_letter]))
    fake_insight_writer.answer = {"paragraph": GOOD, "why": ""}

    client.post(f"/api/outreach/{pending_draft.outreach.id}/insight",
                json={"avoid": ["An earlier attempt nobody liked."]})

    avoid = fake_insight_writer.context["avoid"]
    # Both: what the letter says now, and what the screen has shown since.
    assert in_the_letter in avoid
    assert "An earlier attempt nobody liked." in avoid


def test_the_template_every_company_would_get_is_refused(
    client, repos, pending_draft, fake_insight_writer
):
    fake_insight_writer.answer = {
        "paragraph": (
            "I like that you put drivers and riders on the same side. Most hiring runs "
            "through agents who take a cut and lose the candidate, so the person who needs "
            "the work waits longest. You made it a phone call, which is the kind of thing "
            "I want to build."
        ),
        "why": "",
    }
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight").json()
    assert body["paragraph"] is None


def test_it_offers_every_usable_option_with_its_style(
    client, repos, pending_draft, fake_insight_writer
):
    """Operator request (2026-09-25): three options - excited, curious and
    short - so he picks the one that sounds most like him."""
    short = ("I got curious about how Vahan puts a hiring conversation on a phone call. "
             "That's the kind of problem I want to work on.")
    fake_insight_writer.answer = {"options": [
        {"style": "excited", "paragraph": GOOD},
        {"style": "short", "paragraph": short},
    ], "why": ""}
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight").json()
    assert [o["style"] for o in body["options"]] == ["excited", "short"]
    assert body["paragraph"] == GOOD
    # His own list of what he built is the only thing about him it may use.
    assert "A few things I've built" in fake_insight_writer.context["built"]
