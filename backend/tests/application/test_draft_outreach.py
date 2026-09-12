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


# --- fix round 1 ---------------------------------------------------------------

import logging

from app.domain.states import IllegalTransition
from tests.fakes_repos import DuplicateOutboundMessage


def _send_back_for_redraft(repos, founder_id):
    """A reviewer approves, then sends the draft back: PENDING_APPROVAL ->
    READY_TO_DRAFT is a legal transition (states.py)."""
    (msg,) = repos.message.list_by_outreach_id(founder_id)
    msg.approved_at = "2026-09-04T09:00:00Z"
    msg.edited_by_user = True
    repos.outreach.upsert(founder_id=founder_id, state=str(S.READY_TO_DRAFT))
    return msg


def test_f1_redraft_updates_the_step_0_row_in_place_and_clears_the_old_approval(repos, ready_founder):
    DraftOutreach(FakeDrafter([GOOD]), repos).run(limit=1)
    old = _send_back_for_redraft(repos, ready_founder.id)

    redraft = {**GOOD, "body": GOOD["body"].replace("routing problem", "matching problem")}
    result = DraftOutreach(FakeDrafter([redraft]), repos).run(limit=1)

    assert result.drafted == 1
    rows = repos.message.list_by_outreach_id(ready_founder.id)
    assert len(rows) == 1                                  # never a second outbound step-0 row
    assert rows[0].id == old.id                            # the same row, updated in place
    assert rows[0].body == redraft["body"]
    assert rows[0].approved_at is None                     # old approval voided
    assert rows[0].edited_by_user is False
    assert repos.outreach.get(ready_founder.id).state == S.PENDING_APPROVAL


def test_f1_the_fake_message_repo_really_enforces_the_outbound_uniqueness(repos):
    repos.message.upsert(outreach_id=1, sequence_step=0, direction="outbound", body="a")
    with pytest.raises(DuplicateOutboundMessage):
        repos.message.upsert(outreach_id=1, sequence_step=0, direction="outbound", body="b")
    repos.message.upsert(outreach_id=1, sequence_step=0, direction="inbound", body="reply")  # fine
    repos.message.upsert(outreach_id=1, sequence_step=1, direction="outbound", body="c")     # fine


def test_f1_sequence_step_is_read_from_the_outreach_row(repos, ready_founder):
    # Pretend the first email went out and the founder is back for a follow-up.
    repos.message.upsert(outreach_id=ready_founder.id, sequence_step=0, direction="outbound",
                         body="word " * 60)
    repos.outreach.upsert(founder_id=ready_founder.id, sequence_step=1)
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert d.calls[0]["sequence_step"] == 1
    step1 = repos.message.get_outbound(ready_founder.id, 1)
    assert step1 is not None and step1.body == GOOD["body"]
    assert len(repos.message.list_by_outreach_id(ready_founder.id)) == 2


def test_f1_a_follow_up_longer_than_the_previous_message_is_rejected(repos, ready_founder):
    repos.message.upsert(outreach_id=ready_founder.id, sequence_step=0, direction="outbound",
                         body="Short. Would you take an intern?")
    repos.outreach.upsert(founder_id=ready_founder.id, sequence_step=1)
    d = FakeDrafter([GOOD, GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert any("shorter" in e for e in d.calls[1]["validation_errors"])


UNVERIFIED_CLAIM = {"kind": "skill", "text": "Rust", "source_span": "not in the resume",
                    "span_verified": False}


def test_r3_unverified_claims_are_excluded_from_the_drafter_by_default(repos, ready_founder):
    repos.resume_profile.profile.claims.append(UNVERIFIED_CLAIM)
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert [c["text"] for c in d.calls[0]["claims"]] == ["built a scheduler in Python"]


def test_r3_unverified_claims_do_not_license_the_validator_either(repos, ready_founder):
    repos.resume_profile.profile.claims.append(UNVERIFIED_CLAIM)
    fabricated = {**GOOD, "body": GOOD["body"].replace("in Python", "in Rust")}
    d = FakeDrafter([fabricated, GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert any("Rust" in e for e in d.calls[1]["validation_errors"])


def test_r3_keep_unverified_includes_them_for_drafting_and_validation(repos, ready_founder):
    repos.resume_profile.profile.claims.append(UNVERIFIED_CLAIM)
    uses_rust = {**GOOD, "body": GOOD["body"].replace("in Python", "in Rust")}
    d = FakeDrafter([uses_rust])
    result = DraftOutreach(d, repos, keep_unverified=True).run(limit=1)
    assert [c["text"] for c in d.calls[0]["claims"]] == ["built a scheduler in Python", "Rust"]
    assert result.drafted == 1 and len(d.calls) == 1


def test_r3_all_claims_unverified_refuses_to_draft_and_names_the_override(repos, ready_founder):
    repos.resume_profile.profile.claims = [UNVERIFIED_CLAIM]
    with pytest.raises(ValueError, match="keep-unverified"):
        DraftOutreach(FakeDrafter([GOOD]), repos).run(limit=1)


def _add_ready(repos, founder_id, company_id, name):
    f = repos.founder.add(id=founder_id, company_id=company_id, full_name=name)
    repos.outreach.add(id=founder_id, founder_id=f.id, state=str(S.READY_TO_DRAFT))
    return f


class FailingDrafter:
    """`fail` = set of 1-based call indexes that raise; the rest return GOOD."""

    def __init__(self, fail):
        self.fail, self.calls = set(fail), []

    def draft(self, ctx):
        self.calls.append(ctx)
        if len(self.calls) in self.fail:
            raise RuntimeError("secret provider detail")
        return dict(GOOD)


def test_f3_a_drafter_exception_is_logged_by_type_and_recorded_without_a_transition(repos, ready_founder, caplog):
    d = FailingDrafter(fail={1})
    with caplog.at_level(logging.WARNING, logger="app.application.draft_outreach"):
        result = DraftOutreach(d, repos).run(limit=1)
    assert result.errors == 1 and result.aborted is False
    o = repos.outreach.get(ready_founder.id)
    assert o.state == S.READY_TO_DRAFT                       # no transition
    assert o.last_error == "drafter failed: RuntimeError"    # type name only...
    assert "secret provider detail" not in o.last_error      # ...never the message
    assert any("drafter failed for founder 5: RuntimeError" in r.getMessage() for r in caplog.records)
    assert all("secret provider detail" not in r.getMessage() for r in caplog.records)
    assert len(d.calls) == 1                                 # F14: not re-called after the exception


def test_f14_after_an_exception_the_run_continues_with_exactly_one_call_per_founder(repos, ready_founder):
    _add_ready(repos, 99, ready_founder.company_id, "Second Founder")
    d = FailingDrafter(fail={1})
    result = DraftOutreach(d, repos).run()
    assert len(d.calls) == 2
    assert result.errors == 1 and result.drafted == 1


def test_f3_three_consecutive_exceptions_abort_the_run(repos, ready_founder):
    for i in (97, 98, 99):
        _add_ready(repos, i, ready_founder.company_id, f"Founder {i}")
    d = FailingDrafter(fail={1, 2, 3, 4})
    result = DraftOutreach(d, repos).run()
    assert result.aborted is True
    assert result.errors == 3
    assert len(d.calls) == 3                                 # the fourth founder was never attempted
    assert all(o.state == S.READY_TO_DRAFT for o in repos.outreach.all() if o.id in (5, 97, 98, 99))


def test_f3_a_success_resets_the_consecutive_error_counter(repos, ready_founder):
    for i in (96, 97, 98, 99):
        _add_ready(repos, i, ready_founder.company_id, f"Founder {i}")
    d = FailingDrafter(fail={1, 2, 4, 5})                    # fail, fail, ok, fail, fail
    result = DraftOutreach(d, repos).run()
    assert result.aborted is False
    assert result.errors == 4 and result.drafted == 1
    assert len(d.calls) == 5


def test_f4_founder_name_and_description_terms_are_not_treated_as_fabrications(repos, ready_founder):
    body = ("You're matching blue-collar workers to jobs over voice at Vahan.\n\n"
            "I keep thinking about the gig platforms you place warehouse staff for, Madhav.\n\n"
            "I built a scheduler in Python. Would you take an intern?")
    d = FakeDrafter([{**GOOD, "body": body}])
    result = DraftOutreach(d, repos).run(limit=1)
    assert result.drafted == 1 and len(d.calls) == 1


@pytest.mark.parametrize("length,offered", [(79, False), (80, True)])
def test_f13_long_description_floor_is_exactly_80_characters(repos, ready_founder, length, offered):
    repos.company.get(ready_founder.company_id).long_description = "x" * length
    d = FakeDrafter([GOOD])
    DraftOutreach(d, repos).run(limit=1)
    kinds = [s["kind"] for s in d.calls[0]["available_sources"]]
    assert ("long_description" in kinds) is offered


def test_f15_an_illegal_transition_is_refused_before_anything_is_written(repos, ready_founder, monkeypatch):
    # Simulate a stale listing: the founder was READY_TO_DRAFT when listed
    # but is PENDING_APPROVAL by the time the row is read back.
    listed = repos.founder.list_by_state(S.READY_TO_DRAFT)
    monkeypatch.setattr(repos.founder, "list_by_state", lambda state: listed)
    repos.outreach.upsert(founder_id=ready_founder.id, state=str(S.PENDING_APPROVAL))
    with pytest.raises(IllegalTransition):
        DraftOutreach(FakeDrafter([GOOD]), repos).run(limit=1)
    assert repos.message.list_by_outreach_id(ready_founder.id) == []


def test_f16_a_hook_that_paraphrases_the_one_liner_is_rejected(repos, ready_founder):
    repos.company.get(ready_founder.company_id).one_liner = "voice matching blue-collar workers"
    d = FakeDrafter([GOOD, GOOD])
    DraftOutreach(d, repos).run(limit=1)
    assert any("one_liner" in e for e in d.calls[1]["validation_errors"])


def test_m18_a_launch_title_is_part_of_the_grounding_text(repos, ready_founder):
    repos.news.rows.clear()
    repos.launch.add(company_id=ready_founder.company_id, id=40,
                     title="Vahan launches a driver-screening voice bot", body="Details below.")
    cites_launch = {**GOOD, "research_source_type": "launch", "research_source_id": 40,
                    "body": ("You're screening drivers with a voice bot at Vahan.\n\n"
                             "I built a scheduler in Python.\n\nWould you take an intern?")}
    result = DraftOutreach(FakeDrafter([cites_launch]), repos).run(limit=1)
    assert result.drafted == 1


# --- Final fix wave (F13, ruling FR-9): a founder whose company has since
# left the target band is skipped, not drafted.

def test_a_founder_at_an_out_of_band_company_is_skipped_without_calling_the_drafter(repos, ready_founder):
    from app.domain.targeting import OUT_OF_BAND_STATUS

    repos.company.get(ready_founder.company_id).status = OUT_OF_BAND_STATUS
    d = FakeDrafter([GOOD])
    result = DraftOutreach(d, repos).run()
    assert d.calls == []
    assert result.drafted == 0 and result.skipped_out_of_band == 1
    assert repos.outreach.get(ready_founder.id).state == S.READY_TO_DRAFT
    assert repos.message.get_outbound(ready_founder.id, 0) is None


def test_an_active_company_is_still_drafted(repos, ready_founder):
    repos.company.get(ready_founder.company_id).status = "Active"
    result = DraftOutreach(FakeDrafter([GOOD]), repos).run()
    assert result.drafted == 1 and result.skipped_out_of_band == 0


# --- Final fix wave (F16): every `company_news.source` for the company is
# passed to the validator, so a launch-cited hook cannot pass grounding by
# sharing a term only with a non-cited outlet's name.

def test_hook_sharing_a_term_only_with_a_non_cited_outlet_name_fails_grounding(repos):
    repos.company.add(id=50, domain="fresh.ai", name="Freshly",
                      one_liner="Voice hiring", long_description="x" * 100)
    founder = repos.founder.add(id=50, company_id=50, full_name="Dev Rao")
    repos.outreach.add(id=50, founder_id=founder.id, state=str(S.READY_TO_DRAFT))
    repos.news.add(id=50, company_id=50, title="Freshly raises a seed round for warehouse staffing", source="Mint")
    launch = repos.launch.add(company_id=50, title="Mint condition: voice hiring for warehouses",
                              body="We match warehouse staff over voice calls.", id=50)
    draft = {"subject": "Backend intern at Freshly",
             "body": ("Your Mint play at Freshly caught my eye.\n\n"
                      "I built a scheduler in Python.\n\nWould you take an intern?"),
             "research_source_type": "launch", "research_source_id": launch.id}
    result = DraftOutreach(FakeDrafter([draft, draft]), repos).run()
    assert result.manual_write == 1 and result.drafted == 0
    assert "shares no distinctive term" in repos.outreach.get(50).last_error
