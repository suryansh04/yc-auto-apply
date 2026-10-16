"""The line about the founder (operator request 2026-09-25): where it is
written from, and which lines are refused."""
from types import SimpleNamespace

from app.application.write_founder_line import (
    WEB,
    YC_BIO,
    founder_material,
    run_founder_line_writer,
    unusable_reason,
)

BIO = ("I'm Neel, CBO at Clueso. Prior to Clueso, I built and scaled a consumer "
       "note-taking app while in college. I studied Engineering Design and Data "
       "Science at IIT Madras.")
GOOD = ("I also wanted to write to you specifically because you studied Data Science "
        "at IIT Madras too, and I felt a real overlap with the problems I enjoy solving.")


def _founder(bio=None, web=None, searched=False):
    return SimpleNamespace(id=7, full_name="Neel Balar", bio=bio, web_research=web,
                           web_researched_at=object() if searched else None)


class _Search:
    def __init__(self, results=None, fail=False):
        self.results, self.fail, self.queries = results or [], fail, []

    def search(self, query, max_results=5):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("401 bad key tvly-secret")
        return self.results


class _Repo:
    def __init__(self):
        self.saved = None

    def set_web_research(self, id, text, at):
        self.saved = (id, text)


# --- where it is written from ------------------------------------------------


def test_a_real_yc_bio_is_used_and_nothing_is_searched():
    search = _Search()
    assert founder_material(_founder(bio=BIO), "Clueso", search) == (BIO, YC_BIO)
    assert search.queries == []


def test_a_thin_bio_falls_back_to_the_web_and_caches_what_it_found():
    search = _Search([
        {"title": "Neel Balar - Clueso", "url": "https://x.test/a",
         "content": "Neel Balar co-founded Clueso after building a note app."},
        {"title": "Another Neel Balar", "url": "https://x.test/b",
         "content": "A cricket player from Surat."},
    ])
    repo = _Repo()
    material, source = founder_material(_founder(bio="CBO"), "Clueso", search, founder_repo=repo)
    assert source == WEB
    assert "note app" in material and "cricket" not in material  # must name BOTH
    assert material.startswith("CBO")                               # the bio still leads
    assert repo.saved[0] == 7 and "note app" in repo.saved[1]


def test_a_founder_already_searched_is_not_searched_again():
    search = _Search()
    cached = '[{"title": "Neel at Clueso", "url": "https://x.test/a", "content": "cached result"}]'
    material, source = founder_material(_founder(web=cached, searched=True), "Clueso", search)
    assert source == WEB and "cached result" in material and "https://x.test/a" in material
    assert search.queries == []


def test_a_failed_search_is_not_cached_as_nothing():
    repo = _Repo()
    assert founder_material(_founder(), "Clueso", _Search(fail=True), founder_repo=repo) == ("", "")
    assert repo.saved is None  # tried again next time


def test_no_bio_and_no_search_is_no_material():
    assert founder_material(_founder(), "Clueso", None) == ("", "")


# --- which lines are refused ---------------------------------------------------


def test_his_example_style_passes():
    assert unusable_reason(GOOD, BIO, "Neel Balar", "Clueso") is None


def test_a_line_naming_something_not_in_their_background_is_refused():
    line = "I also noticed you worked at Google before Clueso, which caught my eye."
    assert "not in their background" in unusable_reason(line, BIO, "Neel Balar", "Clueso")


def test_flattery_and_where_it_was_found_are_refused():
    assert unusable_reason("Your journey from IIT Madras to Clueso is really inspiring to me.",
                           BIO, "Neel Balar", "Clueso")
    assert unusable_reason("I saw on your LinkedIn that you studied Data Science at IIT Madras.",
                           BIO, "Neel Balar", "Clueso")


def test_their_name_is_left_to_the_greeting():
    line = "Neel, I found it interesting that you studied Data Science at IIT Madras before this."
    assert "name" in unusable_reason(line, BIO, "Neel Balar", "Clueso")


def test_a_line_about_nothing_they_did_is_refused():
    line = "I also wanted to write to you specifically because I think we would work well together."
    assert "nothing from their background" in unusable_reason(line, BIO, "Neel Balar", "Clueso")


def test_the_writer_keeps_only_usable_options_and_never_raises():
    class Writer:
        def write(self, context):
            return {"options": [{"style": "overlap", "line": GOOD},
                                {"style": "short", "line": "You are amazing."}], "why": "IIT"}

    out = run_founder_line_writer(Writer(), "Neel Balar", "CBO", "Clueso", BIO, YC_BIO)
    assert out == {"options": [{"style": "overlap", "line": GOOD}], "why": "IIT"}

    class Boom:
        def write(self, context):
            raise RuntimeError("key tvly-secret")

    assert run_founder_line_writer(Boom(), "Neel Balar", "CBO", "Clueso", BIO, YC_BIO) is None


# --- at draft time -------------------------------------------------------------


def test_a_new_first_email_gets_a_founder_line(repos):
    from app.application.draft_outreach import DraftOutreach, _to_draft

    class Writer:
        def write(self, context):
            return {"options": [{"style": "overlap", "line": GOOD}], "why": ""}

    draft = _to_draft({"subject": "x", "tie_in": "", "company_paragraphs": ["Tie.", "Curious?"],
                       "could_be_a_user": False}, 0, "Clueso")
    company = SimpleNamespace(name="Clueso")
    out = DraftOutreach(None, repos, founder_writer=Writer())._with_founder_line(
        draft, _founder(bio=BIO), company)
    assert f"Curious? {GOOD}\n\nOutside work:" in out.body
    assert out.founder_line == GOOD


def test_without_a_founder_writer_the_draft_is_unchanged(repos):
    from app.application.draft_outreach import DraftOutreach, _to_draft

    draft = _to_draft({"subject": "x", "company_paragraphs": ["Tie.", "Curious?"],
                       "could_be_a_user": False}, 0, "Clueso")
    assert DraftOutreach(None, repos)._with_founder_line(
        draft, _founder(bio=BIO), SimpleNamespace(name="Clueso")) == draft
