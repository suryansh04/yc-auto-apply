"""`POST /api/outreach/{id}/founder-line` and `.../founder-line/use` -
the line about the founder personally (operator request 2026-09-25)."""
from app.domain import letter

BIO = ("Madhav studied Computer Science at IIT Bombay and spent four years building "
       "speech recognition for Indian languages at Google before starting Vahan.")
LINE = ("I also wanted to write to you specifically because you built speech recognition "
        "for Indian languages, and I felt a real overlap with the problems I enjoy solving.")
COMPANY = [
    "While looking at Vahan, I noticed you run hiring over voice calls. Keeping that "
    "reliable is the kind of backend work I enjoyed most at Better.",
    "The other thing I got curious about is matching. How do you decide who gets which "
    "call? I'd love to learn how your team approaches that.",
]


def _setup(repos, pending_draft, bio=BIO, founder_line=None):
    founder = repos.founder.get(pending_draft.outreach.founder_id)
    founder.bio = bio
    row = repos.message.get_outbound(pending_draft.outreach.id, 0)
    body = letter.assemble_body("Vahan", COMPANY, founder_line=founder_line)
    repos.message.upsert(id=row.id, body=body, founder_line=founder_line)
    return founder, row


def test_it_writes_lines_from_the_yc_bio(client, repos, pending_draft, fake_founder_writer):
    _setup(repos, pending_draft)
    fake_founder_writer.answer = {"options": [{"style": "overlap", "line": LINE}], "why": "Google"}
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/founder-line").json()
    assert body["options"] == [{"style": "overlap", "line": LINE}]
    assert body["source"] == "their YC bio"
    assert body["material"] == BIO  # shown so he can check the line against it
    assert fake_founder_writer.context["material"] == BIO


def test_no_bio_and_no_search_key_says_how_to_get_one(client, repos, pending_draft,
                                                        fake_founder_writer):
    _setup(repos, pending_draft, bio=None)
    repos.web_search = None
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/founder-line").json()
    assert body["options"] == []
    assert "TAVILY_API_KEY" in body["unavailable"]
    assert fake_founder_writer.context is None


def test_a_missing_llm_key_names_the_setting(client, repos, pending_draft):
    _setup(repos, pending_draft)
    repos.founder_writer = None  # the global offline patch makes building one fail
    body = client.post(f"/api/outreach/{pending_draft.outreach.id}/founder-line").json()
    assert body["unavailable"] == "offline test: no founder line writer"


def test_using_a_line_puts_it_at_the_end_of_the_company_part(client, repos, pending_draft):
    _, row = _setup(repos, pending_draft)
    r = client.post(f"/api/outreach/{pending_draft.outreach.id}/founder-line/use", json={"line": LINE})
    assert r.status_code == 200
    stored = repos.message.get_outbound(pending_draft.outreach.id, 0)
    assert f"{COMPANY[1]} {LINE}\n\nOutside work:" in stored.body
    assert stored.founder_line == LINE


def test_a_new_line_replaces_the_old_one(client, repos, pending_draft):
    _setup(repos, pending_draft, founder_line=LINE)
    newer = "You spent years on speech for Indian languages, which is close to what I did."
    client.post(f"/api/outreach/{pending_draft.outreach.id}/founder-line/use", json={"line": newer})
    stored = repos.message.get_outbound(pending_draft.outreach.id, 0)
    assert LINE not in stored.body and newer in stored.body


def test_the_queue_shows_the_line_and_leaves_it_out_of_the_company_part(client, repos, pending_draft):
    """The screen swaps the company part by replacing `insight_text`, so
    the line must not be inside it - or an AI suggestion would erase it."""
    _setup(repos, pending_draft, founder_line=LINE)
    item = client.get(f"/api/queue/{pending_draft.outreach.id}").json()
    assert item["founder_line"] == LINE
    assert item["insight_text"] == "\n\n".join(COMPANY)
