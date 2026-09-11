"""Task 14: `DraftOutreach` (spec §10, D17, D23, D29). The first four tests
are the brief's own, verbatim. The rest pin every branch the brief
describes in prose so that deleting the branch fails a test: the retry cap,
source priority order, the non-intern posting exclusion, the no-material
manual-write path, the claims rule reaching the validator, message
persistence, and the state transition."""

import pytest

from app.application.draft_outreach import DraftOutreach
from app.domain.states import OutreachState as S
from tests.fakes import FakeDrafter

# Deviation from the brief, disclosed: the brief's GOOD body is these same
# four sentences in ONE paragraph, which `validate_style` rejects ("a
# paragraph exceeds 3 sentences", spec §10/D28). The brief's four tests pass
# either way — a manual-write also lands in PENDING_APPROVAL — but the
# stricter tests below need a draft that genuinely validates, so the body is
# split into the spec's three parts. Sentences are unchanged.
GOOD = {"subject": "Backend intern at Vahan",
        "body": ("You're matching blue-collar workers to jobs over voice at Vahan. "
                 "That routing problem is what I keep thinking about.\n\n"
                 "I built a scheduler in Python.\n\n"
                 "Would you take an intern?"),
        "research_source_type": "news", "research_source_id": 1}
BAD = {**GOOD, "body": "I hope this email finds you well. Would you take an intern?"}


def test_valid_draft_reaches_pending_approval(repos, ready_founder):
    DraftOutreach(FakeDrafter([GOOD]), repos).run(limit=1)
    assert repos.outreach.get(ready_founder.id).state == S.PENDING_APPROVAL


def test_invalid_draft_is_retried_exactly_once(repos, ready_founder):
    d = FakeDrafter([BAD, GOOD])
    result = DraftOutreach(d, repos).run(limit=1)
    assert len(d.calls) == 2
    assert result.retried == 1
    assert repos.outreach.get(ready_founder.id).state == S.PENDING_APPROVAL


def test_second_failure_routes_to_manual_write_not_a_silent_drop(repos, ready_founder):
    d = FakeDrafter([BAD, BAD])
    result = DraftOutreach(d, repos).run(limit=1)
    assert len(d.calls) == 2                      # never more than one retry
    assert result.manual_write == 1
    o = repos.outreach.get(ready_founder.id)
    assert o.state == S.PENDING_APPROVAL          # still surfaced to the human
    assert o.last_error                           # with the reasons attached


def test_non_intern_appropriate_posting_is_never_cited(repos, founder_with_senior_posting):
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    ctx = d.calls[0]
    assert all(s["kind"] != "job_posting" for s in ctx["available_sources"])


# --- beyond the brief's four -------------------------------------------------


def test_a_valid_first_draft_calls_the_drafter_exactly_once(repos, ready_founder):
    d = FakeDrafter([GOOD])
    result = DraftOutreach(d, repos).run(limit=1)
    assert len(d.calls) == 1
    assert result.drafted == 1 and result.retried == 0 and result.manual_write == 0


def test_a_third_bad_draft_is_never_requested_even_when_one_is_available(repos, ready_founder):
    # Three responses queued; the cap must stop at two regardless.
    d = FakeDrafter([BAD, BAD, GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert len(d.calls) == 2


def test_retry_context_carries_the_validation_errors_and_the_rejected_draft(repos, ready_founder):
    d = FakeDrafter([BAD, GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert "validation_errors" not in d.calls[0]
    retry = d.calls[1]
    assert any("banned phrase" in e for e in retry["validation_errors"])
    assert retry["previous_draft"]["body"] == BAD["body"]


def test_manual_write_persists_the_rejected_draft_and_the_reasons(repos, ready_founder):
    DraftOutreach(FakeDrafter([BAD, BAD]), repos).run(limit=1)
    o = repos.outreach.get(ready_founder.id)
    assert "banned phrase" in o.last_error
    msgs = repos.message.list_by_outreach_id(o.id)
    assert len(msgs) == 1                         # the rejected draft is shown, not discarded
    assert msgs[0].body == BAD["body"]


def test_a_valid_draft_clears_a_stale_last_error(repos, ready_founder):
    repos.outreach.upsert(founder_id=ready_founder.id, last_error="left over from an earlier run")
    DraftOutreach(FakeDrafter([GOOD]), repos).run(limit=1)
    assert repos.outreach.get(ready_founder.id).last_error is None


def test_the_message_row_records_direction_step_and_source(repos, ready_founder):
    DraftOutreach(FakeDrafter([GOOD]), repos).run(limit=1)
    o = repos.outreach.get(ready_founder.id)
    (msg,) = repos.message.list_by_outreach_id(o.id)
    assert msg.direction == "outbound"
    assert msg.sequence_step == 0
    assert msg.subject == GOOD["subject"] and msg.body == GOOD["body"]
    assert msg.research_source_type == "news" and msg.research_source_id == 1


def test_context_carries_company_founder_sources_and_claims(repos, ready_founder):
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    ctx = d.calls[0]
    assert ctx["company"]["name"] == "Vahan"
    assert ctx["founder"]["full_name"] == "Madhav Krishna"
    assert [c["text"] for c in ctx["claims"]] == ["built a scheduler in Python"]
    assert ctx["available_sources"][0] == {
        "kind": "news", "id": 1,
        "text": "OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI",
        "publication": "The Economic Times",
    }


def test_sources_are_offered_in_priority_order_news_launch_posting_description(repos, ready_founder):
    cid = ready_founder.company_id
    repos.launch.add(company_id=cid, id=40, title="Vahan launches voice hiring",
                     body="We built a voice bot that screens drivers.")
    repos.job_posting.add(id=50, company_id=cid, title="Backend Engineer Intern",
                          employment_type="Internship", min_experience="")
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    kinds = [s["kind"] for s in d.calls[0]["available_sources"]]
    assert kinds == ["news", "launch", "job_posting", "long_description"]


def test_an_intern_appropriate_posting_is_offered(repos, ready_founder):
    repos.job_posting.add(id=50, company_id=ready_founder.company_id,
                          title="Backend Engineer", employment_type="Full-time",
                          min_experience="Any (new grads ok)")
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    postings = [s for s in d.calls[0]["available_sources"] if s["kind"] == "job_posting"]
    assert [p["id"] for p in postings] == [50]
    assert "Backend Engineer" in postings[0]["text"]


def test_a_senior_posting_is_excluded_even_when_it_is_the_only_posting(repos, founder_with_senior_posting):
    # Not a fallback: with news/launch absent and only a senior posting plus a
    # long description, the posting still never reaches the drafter.
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert [s["kind"] for s in d.calls[0]["available_sources"]] == ["long_description"]


def test_citing_the_excluded_senior_posting_fails_grounding_with_the_d23_error(repos, founder_with_senior_posting):
    cites_posting = {**GOOD, "body": "Your Senior Engineer opening at Acme caught my eye. Would you take an intern?",
                     "research_source_type": "job_posting", "research_source_id": 1}
    DraftOutreach(FakeDrafter([cites_posting, cites_posting]), repos).run(limit=1)
    o = repos.outreach.get(founder_with_senior_posting.id)
    assert "not intern-appropriate" in o.last_error


def test_short_long_description_is_not_offered_as_a_source(repos, ready_founder):
    repos.company.get(ready_founder.company_id).long_description = "Voice hiring."  # < 80 chars
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert all(s["kind"] != "long_description" for s in d.calls[0]["available_sources"])


def test_long_description_id_is_the_company_id(repos, ready_founder):
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    (desc,) = [s for s in d.calls[0]["available_sources"] if s["kind"] == "long_description"]
    assert desc["id"] == ready_founder.company_id


def test_a_draft_may_cite_the_long_description(repos, ready_founder):
    repos.news.rows.clear()
    cites_desc = {**GOOD, "research_source_type": "long_description",
                  "research_source_id": ready_founder.company_id}
    result = DraftOutreach(FakeDrafter([cites_desc]), repos).run(limit=1)
    assert result.drafted == 1
    (msg,) = repos.message.list_by_outreach_id(ready_founder.id)
    assert msg.research_source_type == "long_description"


def test_no_research_material_goes_straight_to_manual_write_without_calling_the_drafter(repos, ready_founder):
    repos.news.rows.clear()
    repos.company.get(ready_founder.company_id).long_description = "Voice hiring."
    d = FakeDrafter([GOOD])
    result = DraftOutreach(d, repos).run(limit=1)
    assert d.calls == []
    assert result.manual_write == 1
    o = repos.outreach.get(ready_founder.id)
    assert o.state == S.PENDING_APPROVAL
    assert "no research material" in o.last_error


def test_a_fabricated_technology_is_caught_by_the_claims_rule_and_retried(repos, ready_founder):
    fabricated = {**GOOD, "body": GOOD["body"].replace("in Python", "in Rust")}
    d = FakeDrafter([fabricated, GOOD])
    result = DraftOutreach(d, repos).run(limit=1)
    assert len(d.calls) == 2 and result.retried == 1
    assert any("Rust" in e for e in d.calls[1]["validation_errors"])


def test_only_ready_to_draft_founders_are_processed(repos, ready_founder):
    repos.outreach.upsert(founder_id=ready_founder.id, state=str(S.PENDING_APPROVAL))
    d = FakeDrafter([GOOD])
    result = DraftOutreach(d, repos).run(limit=1)
    assert d.calls == [] and result.drafted == 0


def test_limit_processes_exactly_that_many_founders(repos, ready_founder):
    cid = ready_founder.company_id
    second = repos.founder.add(id=99, company_id=cid, full_name="Second Founder")
    repos.outreach.add(id=99, founder_id=second.id, state=str(S.READY_TO_DRAFT))
    d = FakeDrafter([GOOD, GOOD])
    result = DraftOutreach(d, repos).run(limit=1)
    assert result.drafted == 1 and len(d.calls) == 1
    states = {repos.outreach.get(ready_founder.id).state, repos.outreach.get(99).state}
    assert states == {S.PENDING_APPROVAL, S.READY_TO_DRAFT}


def test_no_limit_processes_every_ready_founder(repos, ready_founder):
    cid = ready_founder.company_id
    second = repos.founder.add(id=99, company_id=cid, full_name="Second Founder")
    repos.outreach.add(id=99, founder_id=second.id, state=str(S.READY_TO_DRAFT))
    result = DraftOutreach(FakeDrafter([GOOD, GOOD]), repos).run()
    assert result.drafted == 2


def test_missing_resume_profile_refuses_to_draft(repos, ready_founder):
    repos.resume_profile.profile = None
    d = FakeDrafter([GOOD])
    with pytest.raises(ValueError):
        DraftOutreach(d, repos).run(limit=1)
    assert d.calls == []
    assert repos.outreach.get(ready_founder.id).state == S.READY_TO_DRAFT


class ExplodingDrafter:
    def __init__(self):
        self.calls = 0

    def draft(self, prompt_context):
        self.calls += 1
        raise RuntimeError("boom")


def test_a_drafter_exception_leaves_the_founder_ready_to_draft_and_continues(repos, ready_founder):
    cid = ready_founder.company_id
    second = repos.founder.add(id=99, company_id=cid, full_name="Second Founder")
    repos.outreach.add(id=99, founder_id=second.id, state=str(S.READY_TO_DRAFT))

    class FailFirst:
        def __init__(self):
            self.calls = []

        def draft(self, ctx):
            self.calls.append(ctx)
            if len(self.calls) == 1:
                raise RuntimeError("boom")
            return dict(GOOD)

    d = FailFirst()
    result = DraftOutreach(d, repos).run()
    assert result.errors == 1 and result.drafted == 1
    states = sorted([repos.outreach.get(ready_founder.id).state, repos.outreach.get(99).state])
    assert states == sorted([S.READY_TO_DRAFT, S.PENDING_APPROVAL])
