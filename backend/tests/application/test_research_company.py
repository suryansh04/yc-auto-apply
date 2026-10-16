"""Research before the letter (his pipeline document, 2026-09-25): facts
only from a source, each with its URL, old ones marked OLD."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.application.research_company import (
    fact_problem,
    gather_sources,
    research_problems,
    run_researcher,
)

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)
COMPANY = SimpleNamespace(id=3, name="UrbanKisaan", slug="urbankisaan", one_liner="Seeds for India",
                          long_description="We breed crops in controlled-environment chambers.",
                          web_research=None, web_researched_at=None)
FOUNDER = SimpleNamespace(id=9, full_name="Vihari Kanukollu", bio="Former farmer in Andhra.",
                          web_research=None, web_researched_at=None)
HF = {"title": "Aadi by UrbanKisaan", "url": "https://huggingface.co/urbankisaan/aadi",
      "content": "UrbanKisaan released Aadi, pretrained on 49 plant genomes.",
      "published_date": "2026-05-01"}


class Search:
    def __init__(self, results):
        self.results, self.queries = results, []

    def search(self, query, max_results=5):
        self.queries.append(query)
        return self.results


class Repo:
    def __init__(self):
        self.saved = None

    def set_web_research(self, id, results, at):
        self.saved = (id, results)


def _sources(search=None, repo=None):
    return gather_sources(COMPANY, FOUNDER, search=search, company_repo=repo, now=NOW)


def test_sources_are_numbered_with_their_urls():
    sources = _sources(Search([HF]), Repo())
    assert [s["id"] for s in sources][:2] == ["S1", "S2"]
    assert sources[0]["url"] == "https://www.ycombinator.com/companies/urbankisaan"
    assert any(s["url"] == HF["url"] for s in sources)


def test_a_web_result_that_does_not_name_the_company_is_dropped():
    other = {**HF, "title": "Something else", "content": "A different seed firm.", "url": "https://x.test"}
    sources = _sources(Search([other]))
    assert not any(s["url"] == "https://x.test" for s in sources)


def test_the_company_search_is_cached():
    repo = Repo()
    _sources(Search([HF]), repo)
    assert repo.saved[0] == 3 and repo.saved[1][0]["url"] == HF["url"]


def test_a_fact_must_be_in_its_source():
    source = "UrbanKisaan released Aadi, pretrained on 49 plant genomes."
    assert fact_problem("Aadi is pretrained on 49 plant genomes", source) is None
    assert "number" in fact_problem("Aadi is pretrained on 60 plant genomes", source)
    assert "Google" in fact_problem("Aadi was built with Google on plant genomes", source)


class Researcher:
    def __init__(self, answer):
        self.answer, self.context = answer, None

    def research(self, context):
        self.context = context
        return self.answer


def test_unsupported_facts_are_dropped_and_old_ones_marked():
    sources = [
        {"id": "S1", "label": "their YC page", "text": COMPANY.long_description, "url": "u1", "date": ""},
        {"id": "S2", "label": "news", "text": "UrbanKisaan founders spent a year on farms", "url": "u2",
         "date": "2022-01-10"},
    ]
    research = run_researcher(Researcher({
        "facts": [
            {"kind": "what_they_do", "text": "breed crops in controlled-environment chambers",
             "source": "S1", "old": False},
            {"kind": "founder_fact", "text": "The founders spent a year on farms", "source": "S2",
             "old": False},
            {"kind": "specific_thing", "text": "Their Aadi model", "source": "S1", "old": False},
        ],
        "question": "How do you clean phenotype data?", "skill_connection": "", "note": "",
    }), "UrbanKisaan", "Vihari Kanukollu", sources, NOW)
    kinds = {f["kind"]: f for f in research["facts"]}
    assert set(kinds) == {"what_they_do", "founder_fact"}
    assert kinds["founder_fact"]["old"] is True            # dated 2022
    assert kinds["founder_fact"]["url"] == "u2"
    assert "specific thing (" in research["note"]          # why it was dropped


def test_a_failed_researcher_never_raises():
    class Boom:
        def research(self, context):
            raise RuntimeError("key sk_secret")

    out = run_researcher(Boom(), "X", "Y", [{"id": "S1", "label": "l", "text": "t", "url": "", "date": ""}])
    assert out["facts"] == [] and "failed" in out["note"]


def test_an_old_fact_must_be_framed_as_i_read():
    research = {"facts": [{"kind": "specific_thing", "old": True,
                           "text": "each group needed one member with a credit history"}]}
    sources = [{"text": "each group needed one member with a credit history"}]
    bad = "Each group needs one member with a credit history. How do you score groups?"
    good = "I read that each group needed one member with a credit history. How do you score groups?"
    assert any("OLD" in e for e in research_problems(bad, research, sources, "Kosh"))
    assert research_problems(good, research, sources, "Kosh") == []


def test_a_name_no_source_mentions_is_refused():
    sources = [{"text": "Aadi is pretrained on 49 plant genomes"}]
    problems = research_problems("I read about Aadi and its Gemini backbone. How?", None, sources)
    assert any("Gemini" in p for p in problems)
