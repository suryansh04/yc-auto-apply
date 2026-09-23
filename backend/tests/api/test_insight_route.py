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


def test_a_company_with_no_description_says_so_rather_than_inventing_one(
    client, repos, pending_draft, fake_insight_writer
):
    repos.company.rows[pending_draft.outreach.id].long_description = None
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/insight").json()

    assert body["paragraph"] is None
    assert "no description" in body["unavailable"]
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
