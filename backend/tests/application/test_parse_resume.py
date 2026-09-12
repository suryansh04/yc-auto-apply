"""Task 13: `ParseResume` turns a resume's raw text into structured, citable
claims (spec §10, D23). The first two tests are the brief's own, verbatim;
the rest pin the guards the brief describes in prose (claim `kind` domain,
verbatim `source_span`) so each can be deleted only by failing a test."""

import pytest

from app.application.parse_resume import ParseResume
from app.domain.entities import ResumeClaim


class FakeParser:
    def parse(self, raw_text):
        return [{"kind": "project", "text": "built a scheduler in Python",
                 "source_span": "Built a scheduler in Python, cut wait 50%"}]


class FakeProfileRepo:
    def __init__(self): self.saved = None
    def save(self, raw_text, claims): self.saved = (raw_text, claims)


RESUME = "Projects\nBuilt a scheduler in Python, cut wait 50%\nSkills: Python, SQL"


def test_claims_are_persisted_with_source_spans():
    repo = FakeProfileRepo()
    claims = ParseResume(FakeParser(), repo).run("resume text")
    assert claims[0].source_span
    assert repo.saved[0] == "resume text"


def test_rejects_empty_resume():
    import pytest
    with pytest.raises(ValueError):
        ParseResume(FakeParser(), FakeProfileRepo()).run("   ")


# --- beyond the brief's two -------------------------------------------------


class ListParser:
    def __init__(self, claims):
        self._claims = claims
        self.calls = []

    def parse(self, raw_text):
        self.calls.append(raw_text)
        return self._claims


def test_blank_resume_never_reaches_the_parser_or_the_repo():
    parser, repo = ListParser([]), FakeProfileRepo()
    with pytest.raises(ValueError):
        ParseResume(parser, repo).run("\n\t ")
    assert parser.calls == []
    assert repo.saved is None


def test_returns_resume_claim_entities_and_persists_plain_dicts():
    parser = ListParser([{"kind": "skill", "text": "Python",
                          "source_span": "Skills: Python, SQL"}])
    repo = FakeProfileRepo()
    claims = ParseResume(parser, repo).run(RESUME)
    assert isinstance(claims[0], ResumeClaim)
    assert claims[0].kind == "skill" and claims[0].text == "Python"
    # What lands in the JSONB column is JSON-serialisable dicts, not
    # dataclass instances.
    raw_text, persisted = repo.saved
    assert raw_text == RESUME
    assert persisted == [{"kind": "skill", "text": "Python",
                          "source_span": "Skills: Python, SQL", "span_verified": True}]


def test_a_claim_whose_span_is_not_verbatim_in_the_resume_is_flagged_not_dropped():
    # The span is the audit trail back to the resume (spec §10). A parser
    # that paraphrases or invents the span breaks that trail. The claim is
    # kept (the brief's own first test feeds "resume text" against a span
    # that can't be in it, so dropping would contradict it) but flagged
    # `span_verified=False`, both on the returned entity and in what is
    # persisted, so `show-claims` can point the operator at it.
    parser = ListParser([
        {"kind": "project", "text": "built a scheduler",
         "source_span": "Built a scheduler in Python, cut wait 50%"},   # verbatim
        {"kind": "metric", "text": "cut wait by half",
         "source_span": "cut wait by 50 percent"},                      # paraphrased
    ])
    repo = FakeProfileRepo()
    use_case = ParseResume(parser, repo)
    claims = use_case.run(RESUME)
    assert [(c.kind, c.span_verified) for c in claims] == [("project", True), ("metric", False)]
    assert [c["kind"] for c in use_case.unverified] == ["metric"]
    assert [c["span_verified"] for c in repo.saved[1]] == [True, False]
    assert use_case.dropped == []


def test_span_match_is_exact_not_case_folded():
    parser = ListParser([{"kind": "project", "text": "built a scheduler",
                          "source_span": "built a scheduler in python, cut wait 50%"}])
    use_case = ParseResume(parser, FakeProfileRepo())
    claims = use_case.run(RESUME)
    assert claims[0].span_verified is False


def test_a_claim_with_an_empty_span_is_flagged_unverified():
    parser = ListParser([{"kind": "skill", "text": "Python", "source_span": ""}])
    use_case = ParseResume(parser, FakeProfileRepo())
    claims = use_case.run(RESUME)
    assert [c.span_verified for c in claims] == [False]
    assert len(use_case.unverified) == 1


def test_a_claim_with_an_unknown_kind_is_dropped():
    parser = ListParser([
        {"kind": "hobby", "text": "chess", "source_span": "Skills: Python, SQL"},
        {"kind": "skill", "text": "SQL", "source_span": "Skills: Python, SQL"},
    ])
    use_case = ParseResume(parser, FakeProfileRepo())
    claims = use_case.run(RESUME)
    assert [c.kind for c in claims] == ["skill"]
    assert [c["kind"] for c in use_case.dropped] == ["hobby"]


@pytest.mark.parametrize("kind", ["skill", "project", "metric", "role", "education"])
def test_every_allowed_kind_is_accepted(kind):
    parser = ListParser([{"kind": kind, "text": "x", "source_span": "Skills: Python, SQL"}])
    claims = ParseResume(parser, FakeProfileRepo()).run(RESUME)
    assert [c.kind for c in claims] == [kind]


GOOD_CLAIM = {"kind": "skill", "text": "SQL", "source_span": "Skills: Python, SQL"}


def test_a_claim_with_empty_text_is_dropped():
    parser = ListParser([{"kind": "skill", "text": "  ", "source_span": "Skills: Python, SQL"},
                         GOOD_CLAIM])
    use_case = ParseResume(parser, FakeProfileRepo())
    assert [c.text for c in use_case.run(RESUME)] == ["SQL"]
    assert len(use_case.dropped) == 1


def test_the_parser_receives_the_raw_text_unchanged():
    parser = ListParser([GOOD_CLAIM])
    ParseResume(parser, FakeProfileRepo()).run(RESUME)
    assert parser.calls == [RESUME]


# --- fix round 1 ---------------------------------------------------------------


def test_f6_a_parser_run_with_no_usable_claims_does_not_replace_the_profile():
    repo = FakeProfileRepo()
    repo.saved = ("old resume", [GOOD_CLAIM])          # a good profile already exists
    for parser in (ListParser([]),                       # parser returned nothing
                   ListParser([{"kind": "hobby", "text": "x", "source_span": "Skills: Python, SQL"}])):
        with pytest.raises(ValueError, match="no usable claims"):
            ParseResume(parser, repo).run(RESUME)
        assert repo.saved == ("old resume", [GOOD_CLAIM])


def test_f8_a_span_crossing_a_pdf_line_wrap_is_still_verified():
    wrapped = "Projects\nBuilt a scheduler in\nPython, cut  wait 50%\nSkills: Python, SQL"
    parser = ListParser([{"kind": "project", "text": "built a scheduler",
                          "source_span": "Built a scheduler in Python, cut wait 50%"}])
    claims = ParseResume(parser, FakeProfileRepo()).run(wrapped)
    assert claims[0].span_verified is True
    assert claims[0].source_span == "Built a scheduler in Python, cut wait 50%"  # stored as given


def test_f8_a_span_carrying_the_pdf_line_wrap_matches_a_single_spaced_resume():
    # The other direction: the model copied the wrapped text (line break,
    # doubled space) while the stored resume text is single-spaced.
    parser = ListParser([{"kind": "project", "text": "built a scheduler",
                          "source_span": "Built a scheduler in\nPython, cut  wait 50%"}])
    claims = ParseResume(parser, FakeProfileRepo()).run(RESUME)
    assert claims[0].span_verified is True


def test_f8_whitespace_normalisation_does_not_fold_case():
    parser = ListParser([{"kind": "project", "text": "built a scheduler",
                          "source_span": "built a scheduler in\npython, cut wait 50%"}])
    claims = ParseResume(parser, FakeProfileRepo()).run(RESUME)
    assert claims[0].span_verified is False


def test_f17_kind_is_case_folded():
    parser = ListParser([{"kind": "Skill", "text": "SQL", "source_span": "Skills: Python, SQL"}])
    claims = ParseResume(parser, FakeProfileRepo()).run(RESUME)
    assert claims[0].kind == "skill"
