# YC Auto Apply — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A command-line pipeline that discovers 144 Indian YC companies, scrapes their ~269 founders and research material, resolves founder email addresses, drafts a short internship enquiry per founder, and sends approved drafts from the user's Gmail — with every draft reviewed by a human before it leaves.

**Architecture:** Modular monolith, ports-and-adapters. `domain/` is pure Python with no IO. `application/` holds one use case per pipeline stage, receiving ports by constructor injection. `adapters/` implements those ports against YC, Reoon, Gemini and Gmail. Phase 1 invokes the use cases through a Typer CLI; Phase 2 wraps the same use cases in the seven worker loops of D20 without changing them.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, Alembic, Postgres 16, httpx, Pydantic v2 + pydantic-settings, Typer, pytest, respx, google-genai, google-api-python-client.

**Spec:** `docs/superpowers/specs/2026-09-03-yc-auto-apply-design.md` — read it alongside this plan. Every `D<n>` reference below points at a numbered decision there.

## Global Constraints

- **Python 3.11.** Verified present on the target machine.
- **`pytest` must pass with no environment variables set and no network access.** This is the definition of done for every task. Adapter tests that need real credentials are marked `@pytest.mark.integration` and skip cleanly.
- **`domain/` imports no framework, no ORM, and performs no IO.** `domain/` and `application/` may never import `adapters/`. Dependencies point inward.
- **Nothing sends without `approved_at` set** — enforced in the use case *and* by a database `CHECK` constraint (D5).
- **Target band: India + Active + `1 <= team_size <= 200`** → 144 companies, ~269 founders (D25).
- **Job postings are never a targeting or ranking signal** (D24). Only a citation source, and only when intern-appropriate (D23).
- **The email asks for an internship**, is ~90 words with a hard cap of 120, and never mentions location (D27, D28).
- **The research source never appears in the email body** — no outlet names, no "I saw the article" (D29).
- Secrets come from env vars via a typed settings object. `.env` is git-ignored; `.env.example` documents every variable.

## Phase 1 scope

**In:** company sync, founder + research scraping, email resolution, resume parsing, drafting with the deterministic validator, terminal review, Gmail sending.

**Deferred to Phase 2:** the React dashboard, reply polling and classification, follow-up scheduling, the voice critic (D22), and the seven long-running worker processes (D20). The `outcomes` table and the `messages.critic_*` columns are deliberately **not** created in Phase 1 — Phase 2 adds them in its own migration.

## File Structure

```
backend/
  pyproject.toml
  alembic.ini
  .env.example
  docker-compose.yml
  alembic/versions/
  app/
    config.py            Settings (pydantic-settings), typed env access
    domain/
      entities.py        Company, Founder, JobPosting, NewsItem, LaunchPost,
                         Outreach, Draft, ResumeClaim — plain dataclasses
      states.py          OutreachState enum + legal transition table
      targeting.py       is_target_company()
      names.py           normalise_name()
      candidates.py      generate_candidates()
      interns.py         is_intern_appropriate()
      validation.py      validate_draft() + ValidationError list
      ranking.py         rank_key()
    ports/
      __init__.py        All Protocols in one module — they are short
    adapters/
      yc_oss.py          YcOssClient
      yc_page.py         YcPageScraper
      email_verifier.py  MyEmailVerifierClient (D30)
      gemini.py          GeminiDrafter, GeminiResumeParser
      gmail.py           GmailSender
      db/models.py       SQLAlchemy models
      db/repositories.py Repository implementations
    application/
      sync_companies.py  scrape_company_page.py  resolve_emails.py
      parse_resume.py    draft_outreach.py       send_approved.py
    cli.py               Typer app: every stage + the review loop
  tests/
    domain/  application/  adapters/  fakes.py  conftest.py
```

Each domain module is one file with one responsibility, small enough to hold in context. `ports/__init__.py` is a single module because Protocols are a few lines each and splitting them across files adds navigation cost without benefit.

---

## Task 1: Project scaffolding, config and database

**Files:**
- Create: `backend/pyproject.toml`, `backend/docker-compose.yml`, `backend/.env.example`, `backend/alembic.ini`
- Create: `backend/app/config.py`, `backend/app/__init__.py`, `backend/tests/conftest.py`
- Modify: `.gitignore` (repo root — add `.env`, `__pycache__/`, `.venv/`, `*.pyc`)

**Interfaces:**
- Consumes: nothing.
- Produces: `app.config.Settings` with fields `database_url: str`, `email_verifier_api_key: str = ""`, `gemini_api_key: str = ""`, `gemini_model: str = "gemini-3-flash"`, `gemini_tier: Literal["free","paid"] = "free"`, `daily_send_cap: int = 20`. `get_settings() -> Settings` is `lru_cache`d.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config.py
from app.config import Settings

def test_settings_reads_defaults_without_env():
    s = Settings(database_url="postgresql://x/y")
    assert s.gemini_model == "gemini-3-flash"
    assert s.gemini_tier == "free"
    assert s.daily_send_cap == 20

def test_paid_tier_is_explicit_opt_in():
    s = Settings(database_url="postgresql://x/y", gemini_tier="paid")
    assert s.gemini_tier == "paid"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "yc-auto-apply"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "sqlalchemy>=2.0", "alembic>=1.13", "psycopg[binary]>=3.1",
  "httpx>=0.27", "pydantic>=2.7", "pydantic-settings>=2.3",
  "typer>=0.12", "google-genai>=0.3",
  "google-api-python-client>=2.130", "google-auth-oauthlib>=1.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.2", "respx>=0.21", "pytest-cov>=5.0"]

[tool.pytest.ini_options]
markers = ["integration: needs real credentials and network"]
addopts = "-m 'not integration'"

[tool.setuptools.packages.find]
include = ["app*"]
```

- [ ] **Step 4: Write `app/config.py`**

```python
from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    email_verifier_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash"
    gemini_tier: Literal["free", "paid"] = "free"
    daily_send_cap: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 5: Run the test — it should pass**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Add docker-compose and .env.example**

```yaml
# backend/docker-compose.yml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: yc
      POSTGRES_PASSWORD: yc
      POSTGRES_DB: ycautoapply
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
volumes:
  pgdata:
```

```bash
# backend/.env.example
DATABASE_URL=postgresql+psycopg://yc:yc@localhost:5432/ycautoapply
EMAIL_VERIFIER_API_KEY=
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3-flash
GEMINI_TIER=free
DAILY_SEND_CAP=20
```

- [ ] **Step 7: Initialise Alembic**

Run: `cd backend && alembic init alembic`
Then set `sqlalchemy.url` in `alembic/env.py` to read `get_settings().database_url` rather than hardcoding it.

- [ ] **Step 8: Verify the database starts**

Run: `cd backend && docker compose up -d && docker compose ps`
Expected: the `db` service is running and healthy.

- [ ] **Step 9: Commit**

```bash
git add backend .gitignore
git commit -m "feat: scaffold backend, typed settings, postgres and alembic"
```

---

## Task 2: Domain — targeting filter

**Files:**
- Create: `backend/app/domain/targeting.py`, `backend/app/domain/__init__.py`
- Test: `backend/tests/domain/test_targeting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_target_company(raw: dict) -> bool` — takes a raw `yc-oss` company dict.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_targeting.py
import pytest
from app.domain.targeting import is_target_company

def co(**kw):
    base = {"regions": ["India", "South Asia"], "all_locations": "Bengaluru, KA, India",
            "status": "Active", "team_size": 60}
    base.update(kw)
    return base

@pytest.mark.parametrize("team_size,expected", [
    (1, True), (10, True), (11, True), (200, True), (201, False), (0, False),
])
def test_team_size_band_is_1_to_200(team_size, expected):
    assert is_target_company(co(team_size=team_size)) is expected

def test_requires_india_in_regions_or_locations():
    assert is_target_company(co(regions=["United States of America"],
                                all_locations="Boston, MA, USA")) is False
    assert is_target_company(co(regions=[], all_locations="Hyderabad, India")) is True

def test_requires_active_status():
    assert is_target_company(co(status="Acquired")) is False
    assert is_target_company(co(status="Inactive")) is False

def test_missing_or_non_int_team_size_is_excluded():
    assert is_target_company(co(team_size=None)) is False
    assert is_target_company(co(team_size="60")) is False

def test_swipe_is_in_the_target_set():
    # The reference case for D24 - zero postings, still a valid target.
    swipe = co(team_size=60, all_locations="Hyderabad, Telangana, India")
    assert is_target_company(swipe) is True
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/domain/test_targeting.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.targeting'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/domain/targeting.py
"""Company targeting filter. See spec D25: band is 1-200, not 11-200."""

MIN_TEAM_SIZE = 1
MAX_TEAM_SIZE = 200


def is_target_company(raw: dict) -> bool:
    if (raw.get("status") or "").lower() != "active":
        return False
    team_size = raw.get("team_size")
    if not isinstance(team_size, int) or isinstance(team_size, bool):
        return False
    if not MIN_TEAM_SIZE <= team_size <= MAX_TEAM_SIZE:
        return False
    haystack = " ".join(raw.get("regions") or []) + " " + (raw.get("all_locations") or "")
    return "india" in haystack.lower()
```

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/domain/test_targeting.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/targeting.py backend/tests/domain/test_targeting.py
git commit -m "feat: company targeting filter for the 1-200 India band"
```

---

## Task 3: Domain — name normalisation and email candidate patterns

**Files:**
- Create: `backend/app/domain/names.py`, `backend/app/domain/candidates.py`
- Test: `backend/tests/domain/test_candidates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalise_name(full_name: str) -> tuple[str, str]` returning `(first, last)`, `last` empty for a mononym. `generate_candidates(full_name: str, domain: str) -> list[Candidate]` where `Candidate` is a frozen dataclass with `address: str`, `pattern: str`, `priority: int` (1-based, ascending = higher priority).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_candidates.py
from app.domain.names import normalise_name
from app.domain.candidates import generate_candidates

def test_normalise_strips_diacritics_and_case():
    assert normalise_name("José Ángel") == ("jose", "angel")

def test_three_part_indian_name_uses_first_and_last_token():
    assert normalise_name("Suryansh Pratap Singh") == ("suryansh", "singh")

def test_mononym_has_empty_last():
    assert normalise_name("Madhav") == ("madhav", "")

def test_candidate_order_matches_spec_priority():
    got = [c.address for c in generate_candidates("Pratish Gandhi", "onefin.com")]
    assert got == [
        "pratish@onefin.com",
        "pratish.gandhi@onefin.com",
        "pgandhi@onefin.com",
        "pratishgandhi@onefin.com",
        "pratish_gandhi@onefin.com",
        "pratishg@onefin.com",
        "gandhi@onefin.com",
    ]

def test_priorities_are_sequential_from_one():
    cands = generate_candidates("Pratish Gandhi", "onefin.com")
    assert [c.priority for c in cands] == [1, 2, 3, 4, 5, 6, 7]

def test_mononym_produces_only_first_patterns():
    got = [c.address for c in generate_candidates("Madhav", "vahan.ai")]
    assert got == ["madhav@vahan.ai"]

def test_no_duplicate_addresses():
    cands = generate_candidates("Ram Ram", "x.com")
    assert len({c.address for c in cands}) == len(cands)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/domain/test_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.names'`

- [ ] **Step 3: Write `names.py`**

```python
import re
import unicodedata


def _clean(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", ascii_only.lower())


def normalise_name(full_name: str) -> tuple[str, str]:
    tokens = [t for t in (_clean(p) for p in full_name.split()) if t]
    if not tokens:
        return ("", "")
    if len(tokens) == 1:
        return (tokens[0], "")
    return (tokens[0], tokens[-1])
```

- [ ] **Step 4: Write `candidates.py`**

```python
from dataclasses import dataclass

from app.domain.names import normalise_name


@dataclass(frozen=True)
class Candidate:
    address: str
    pattern: str
    priority: int


# Ordered by how common each shape is at startups. Order matters: it is what
# keeps a full run inside the verification budget via early exit (spec §9).
_PATTERNS: list[tuple[str, str]] = [
    ("first", "{first}"),
    ("first.last", "{first}.{last}"),
    ("f_last", "{f}{last}"),
    ("firstlast", "{first}{last}"),
    ("first_last", "{first}_{last}"),
    ("first_l", "{first}{l}"),
    ("last", "{last}"),
]


def generate_candidates(full_name: str, domain: str) -> list[Candidate]:
    first, last = normalise_name(full_name)
    if not first:
        return []
    parts = {"first": first, "last": last, "f": first[0], "l": last[0] if last else ""}

    out: list[Candidate] = []
    seen: set[str] = set()
    for name, template in _PATTERNS:
        if not last and "{last}" in template:
            continue
        local = template.format(**parts)
        address = f"{local}@{domain}"
        if address in seen:
            continue
        seen.add(address)
        out.append(Candidate(address=address, pattern=name, priority=len(out) + 1))
    return out
```

- [ ] **Step 5: Run the tests — they should pass**

Run: `cd backend && pytest tests/domain/test_candidates.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/names.py backend/app/domain/candidates.py backend/tests/domain/test_candidates.py
git commit -m "feat: founder email candidate generation with priority ordering"
```

---

## Task 4: Domain — intern-appropriateness predicate

**Files:**
- Create: `backend/app/domain/interns.py`
- Test: `backend/tests/domain/test_interns.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_intern_appropriate(employment_type: str | None, title: str | None, min_experience: str | None) -> bool`.

Table-driven against the real posting shapes measured in spec §3. `type` alone is insufficient — 4 of 15 intern-titled roles were typed `Full-time`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_interns.py
import pytest
from app.domain.interns import is_intern_appropriate

@pytest.mark.parametrize("etype,title,exp,expected", [
    # Real shapes observed in the YC payload (spec §3).
    ("Internship", "Vision Engineer Intern (AI & Computer Vision)", "", True),
    ("Internship", "Marketing Intern", None, True),
    ("Full-time", "UI UX Intern", "Any (new grads ok)", True),
    ("Full-time", "Founder's Office - Intern", "Any (new grads ok)", True),
    ("Full-time", "Backend Engineer", "Any (new grads ok)", True),
    ("Full-time", "Senior Engineer", "6+ years", False),
    ("Full-time", "Engineering Manager", "3+ years", False),
    ("Full-time", "Lead Product Designer", "6+ years", False),
    ("Full-time", "Site Reliability Engineer", "1+ years", False),
])
def test_intern_appropriateness(etype, title, exp, expected):
    assert is_intern_appropriate(etype, title, exp) is expected

def test_case_insensitive_on_title():
    assert is_intern_appropriate("Full-time", "GRAPHIC DESIGN INTERN", "3+ years") is True

def test_all_none_is_not_appropriate():
    assert is_intern_appropriate(None, None, None) is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/domain/test_interns.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.interns'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/domain/interns.py
"""D23: a job posting may only be cited when it is intern-appropriate."""

_OPEN_EXPERIENCE = {"", "any (new grads ok)", "none"}


def is_intern_appropriate(
    employment_type: str | None,
    title: str | None,
    min_experience: str | None,
) -> bool:
    if (employment_type or "").strip().lower() == "internship":
        return True
    if "intern" in (title or "").lower():
        return True
    return (min_experience or "").strip().lower() in _OPEN_EXPERIENCE and bool(title)
```

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/domain/test_interns.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/interns.py backend/tests/domain/test_interns.py
git commit -m "feat: intern-appropriateness predicate over posting type, title and experience"
```

---

## Task 5: Domain — outreach state machine

**Files:**
- Create: `backend/app/domain/states.py`, `backend/app/domain/entities.py`
- Test: `backend/tests/domain/test_states.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `OutreachState` (str enum) and `assert_transition(src: OutreachState, dst: OutreachState) -> None`, raising `IllegalTransition`. Phase 1 uses a subset of states; the follow-up states exist but are unreachable until Phase 2.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_states.py
import pytest
from app.domain.states import OutreachState as S, assert_transition, IllegalTransition

def test_happy_path_transitions_are_legal():
    for src, dst in [
        (S.NEW, S.NEEDS_EMAIL),
        (S.NEEDS_EMAIL, S.READY_TO_DRAFT),
        (S.NEEDS_EMAIL, S.NEEDS_MANUAL_EMAIL),
        (S.NEEDS_MANUAL_EMAIL, S.READY_TO_DRAFT),
        (S.NEEDS_MANUAL_EMAIL, S.UNRESOLVED),
        (S.READY_TO_DRAFT, S.PENDING_APPROVAL),
        (S.PENDING_APPROVAL, S.APPROVED),
        (S.PENDING_APPROVAL, S.DISCARDED),
        (S.APPROVED, S.SENT),
    ]:
        assert_transition(src, dst)

def test_sending_is_only_legal_from_approved():
    # The invariant that keeps an unapproved email from ever going out.
    for src in [S.NEW, S.READY_TO_DRAFT, S.PENDING_APPROVAL, S.NEEDS_EMAIL]:
        with pytest.raises(IllegalTransition):
            assert_transition(src, S.SENT)

def test_terminal_states_cannot_transition_out():
    for terminal in [S.DISCARDED, S.UNRESOLVED, S.BOUNCED]:
        with pytest.raises(IllegalTransition):
            assert_transition(terminal, S.READY_TO_DRAFT)

def test_any_non_terminal_may_fail():
    assert_transition(S.READY_TO_DRAFT, S.FAILED)
    assert_transition(S.NEEDS_EMAIL, S.FAILED)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/domain/test_states.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.states'`

- [ ] **Step 3: Write `states.py`**

```python
from enum import StrEnum


class IllegalTransition(Exception):
    pass


class OutreachState(StrEnum):
    NEW = "new"
    NEEDS_EMAIL = "needs_email"
    NEEDS_MANUAL_EMAIL = "needs_manual_email"
    UNRESOLVED = "unresolved"
    READY_TO_DRAFT = "ready_to_draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    REPLIED = "replied"          # Phase 2
    CLOSED = "closed"            # Phase 2
    DISCARDED = "discarded"
    BOUNCED = "bounced"
    FAILED = "failed"


S = OutreachState
TERMINAL = {S.UNRESOLVED, S.DISCARDED, S.BOUNCED, S.CLOSED}

_LEGAL: dict[OutreachState, set[OutreachState]] = {
    S.NEW: {S.NEEDS_EMAIL},
    S.NEEDS_EMAIL: {S.READY_TO_DRAFT, S.NEEDS_MANUAL_EMAIL},
    S.NEEDS_MANUAL_EMAIL: {S.READY_TO_DRAFT, S.UNRESOLVED},
    S.READY_TO_DRAFT: {S.PENDING_APPROVAL},
    S.PENDING_APPROVAL: {S.APPROVED, S.DISCARDED, S.READY_TO_DRAFT},
    S.APPROVED: {S.SENT},
    S.SENT: {S.REPLIED, S.READY_TO_DRAFT, S.CLOSED, S.BOUNCED},
    S.REPLIED: {S.CLOSED},
}


def assert_transition(src: OutreachState, dst: OutreachState) -> None:
    if dst is S.FAILED and src not in TERMINAL:
        return
    if dst not in _LEGAL.get(src, set()):
        raise IllegalTransition(f"{src} -> {dst} is not a legal transition")
```

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/domain/test_states.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add the entity dataclasses**

```python
# backend/app/domain/entities.py
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Company:
    yc_id: int
    slug: str
    name: str
    website: str | None
    domain: str | None
    batch: str | None
    team_size: int | None
    status: str | None
    is_hiring: bool
    one_liner: str | None
    long_description: str | None


@dataclass
class Founder:
    yc_user_id: int
    company_id: int
    full_name: str
    title: str | None
    linkedin_url: str | None
    twitter_url: str | None
    bio: str | None


@dataclass
class JobPosting:
    company_id: int
    title: str
    employment_type: str | None
    min_experience: str | None
    location: str | None
    url: str | None


@dataclass
class NewsItem:
    company_id: int
    title: str
    url: str
    source: str | None
    published_at: datetime | None


@dataclass
class LaunchPost:
    company_id: int
    title: str
    body: str
    published_email: str | None


@dataclass
class ResumeClaim:
    kind: str          # skill | project | metric | role | education
    text: str
    source_span: str


@dataclass
class Draft:
    subject: str
    body: str
    research_source_type: str        # news | launch | job_posting | long_description
    research_source_id: int | None
    sequence_step: int = 0
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/states.py backend/app/domain/entities.py backend/tests/domain/test_states.py
git commit -m "feat: outreach state machine with SENT reachable only from APPROVED"
```

---

## Task 6: Domain — slop validator, concision and style rules

**Files:**
- Create: `backend/app/domain/validation.py`
- Test: `backend/tests/domain/test_validation_style.py`

**Interfaces:**
- Consumes: `Draft` from `app.domain.entities`.
- Produces: `validate_style(draft: Draft, previous_body: str | None = None) -> list[str]` returning human-readable failure reasons, empty when the draft passes. Task 7 adds `validate_grounding(...)`; `validate_draft(...)` composes both.

Rules from spec §10 (D28): ≤120 words, no sentence >25 words, ≤3 paragraphs of ≤3 sentences, no semicolons, ≤half of sentences starting "I", banned phrases, ≤2 em dashes, subject 3–6 words with no emoji or all-caps, no seniority phrases, no location mentions, an internship ask must be present, follow-ups shorter than the message before.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_validation_style.py
from app.domain.entities import Draft
from app.domain.validation import validate_style

GOOD_BODY = (
    "You're matching blue-collar workers to jobs over voice, in a dozen languages. "
    "That routing problem is the part I keep thinking about.\n\n"
    "I'm a final-year CS student. I built a scheduler that cut queue wait by half. "
    "Python and Postgres mostly.\n\n"
    "Would you take an intern this summer? Happy to send code."
)

def draft(body=GOOD_BODY, subject="Backend intern at Vahan", step=0):
    return Draft(subject=subject, body=body, research_source_type="news",
                 research_source_id=1, sequence_step=step)

def test_the_reference_example_passes():
    assert validate_style(draft()) == []

def test_rejects_body_over_120_words():
    assert any("120 words" in e for e in validate_style(draft(body="word " * 121 + "intern")))

def test_rejects_a_sentence_over_25_words():
    long_sentence = " ".join(["word"] * 26) + " intern."
    assert any("25 words" in e for e in validate_style(draft(body=long_sentence)))

def test_rejects_more_than_three_paragraphs():
    body = "\n\n".join(["An intern ask here."] * 4)
    assert any("paragraph" in e for e in validate_style(draft(body=body)))

def test_rejects_semicolons():
    body = "You build voice AI; I want an internship."
    assert any("semicolon" in e for e in validate_style(draft(body=body)))

def test_rejects_more_than_half_sentences_starting_with_I():
    body = "I built a thing. I like this. I want an internship. You do voice."
    assert any('"I"' in e for e in validate_style(draft(body=body)))

def test_rejects_banned_phrases():
    body = "I hope this email finds you well. Would you take an intern?"
    assert any("banned phrase" in e for e in validate_style(draft(body=body)))

def test_rejects_seniority_claims():
    body = "I have three years of experience. Would you take an intern?"
    assert any("seniority" in e for e in validate_style(draft(body=body)))

def test_rejects_location_mentions():
    body = "I can work remote. Would you take an intern?"
    assert any("location" in e for e in validate_style(draft(body=body)))

def test_rejects_missing_internship_ask():
    body = "You build voice AI. I built a scheduler. Would you chat?"
    assert any("internship ask" in e for e in validate_style(draft(body=body)))

def test_rejects_bad_subject_length():
    assert any("subject" in e for e in validate_style(draft(subject="Hi")))
    assert any("subject" in e for e in validate_style(
        draft(subject="A rather long subject line about interning here")))

def test_followup_must_be_shorter_than_previous():
    errs = validate_style(draft(step=1), previous_body="short intern ask")
    assert any("shorter" in e for e in errs)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/domain/test_validation_style.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.validation'`

- [ ] **Step 3: Write the style validator**

```python
# backend/app/domain/validation.py
"""Deterministic anti-slop rules. Spec §10. These are domain code, not prompt
instructions, precisely so they can be unit-tested and cannot be argued with."""
import re

MAX_WORDS = 120
MAX_SENTENCE_WORDS = 25
MAX_PARAGRAPHS = 3
MAX_SENTENCES_PER_PARAGRAPH = 3
MAX_EM_DASHES = 2

BANNED_PHRASES = [
    "i hope this email finds you well", "i am reaching out", "passionate about",
    "leverage", "synergy", "game-changing", "i came across your company",
    "delve", "tapestry", "in today's fast-paced", "reach out", "circle back",
    "at your earliest convenience", "i would love the opportunity",
    "please do not hesitate",
]
SENIORITY_PHRASES = [
    "years of experience", "in my current role", "my team", "i led a team",
    "throughout my career",
]
LOCATION_PHRASES = ["remote", "relocate", "based in", "work from"]
INTERN_TOKENS = ["internship", "intern", "interning"]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\S+", text) if w]


def validate_style(draft, previous_body: str | None = None) -> list[str]:
    errs: list[str] = []
    body = draft.body
    low = body.lower()

    if len(_words(body)) > MAX_WORDS:
        errs.append(f"body exceeds {MAX_WORDS} words")

    for s in _sentences(body):
        if len(_words(s)) > MAX_SENTENCE_WORDS:
            errs.append(f"a sentence exceeds {MAX_SENTENCE_WORDS} words: {s[:40]!r}")
            break

    paragraphs = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    if len(paragraphs) > MAX_PARAGRAPHS:
        errs.append(f"more than {MAX_PARAGRAPHS} paragraphs")
    for p in paragraphs:
        if len(_sentences(p)) > MAX_SENTENCES_PER_PARAGRAPH:
            errs.append("a paragraph exceeds 3 sentences")
            break

    if ";" in body:
        errs.append("contains a semicolon")

    sents = _sentences(body)
    if sents:
        i_starts = sum(1 for s in sents if re.match(r"^I\b", s))
        if i_starts * 2 > len(sents):
            errs.append('more than half the sentences begin with "I"')

    for phrase in BANNED_PHRASES:
        if phrase in low:
            errs.append(f"banned phrase: {phrase!r}")
    for phrase in SENIORITY_PHRASES:
        if phrase in low:
            errs.append(f"implies seniority: {phrase!r}")
    for phrase in LOCATION_PHRASES:
        if phrase in low:
            errs.append(f"raises location: {phrase!r}")

    if not any(t in low for t in INTERN_TOKENS):
        errs.append("no explicit internship ask")

    if body.count("—") > MAX_EM_DASHES:
        errs.append("more than two em dashes")

    subject_words = _words(draft.subject)
    if not 3 <= len(subject_words) <= 6:
        errs.append("subject is not between three and six words")
    if any(w.isupper() and len(w) > 1 for w in subject_words):
        errs.append("subject contains an all-caps word")
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", draft.subject):
        errs.append("subject contains emoji")

    if draft.sequence_step > 0 and previous_body is not None:
        if len(_words(body)) >= len(_words(previous_body)):
            errs.append("follow-up is not shorter than the previous message")

    return errs
```

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/domain/test_validation_style.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/validation.py backend/tests/domain/test_validation_style.py
git commit -m "feat: concision and style half of the anti-slop validator"
```

---

## Task 7: Domain — slop validator, grounding and truthfulness rules

**Files:**
- Modify: `backend/app/domain/validation.py`
- Test: `backend/tests/domain/test_validation_grounding.py`

**Interfaces:**
- Consumes: `Draft`, `ResumeClaim`, and a `SourceRow` describing the cited research row.
- Produces: `validate_grounding(draft, source, company_name, one_liner, claims) -> list[str]` and `validate_draft(...)` composing style + grounding. `SourceRow` is a frozen dataclass: `kind: str`, `text: str`, `publication: str | None`, `intern_appropriate: bool | None`.

Rules (D17, D23, D29): the source must resolve; the hook must share a distinctive term with the source's *substance*; the shared term must not be the publication name; no source-as-subject phrasing; no publication name in the body; a cited `job_posting` must be intern-appropriate; the company must be named; no resume claims absent from `claims`; hook not >60% overlap with `one_liner`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/domain/test_validation_grounding.py
from app.domain.entities import Draft, ResumeClaim
from app.domain.validation import validate_grounding, SourceRow

NEWS = SourceRow(
    kind="news",
    text="OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI",
    publication="The Economic Times",
    intern_appropriate=None,
)
CLAIMS = [ResumeClaim(kind="project", text="built a scheduler in Python", source_span="...")]
GOOD = ("You're matching blue-collar workers to jobs over voice at Vahan. "
        "That routing problem is what I keep thinking about. "
        "I built a scheduler in Python. Would you take an intern?")

def d(body=GOOD, stype="news", sid=1):
    return Draft(subject="Backend intern at Vahan", body=body,
                 research_source_type=stype, research_source_id=sid)

def test_grounded_draft_passes():
    assert validate_grounding(d(), NEWS, "Vahan", "Recruitment over voice", CLAIMS) == []

def test_unresolved_source_is_rejected():
    errs = validate_grounding(d(sid=None), None, "Vahan", "x", CLAIMS)
    assert any("does not resolve" in e for e in errs)

def test_rejects_source_as_subject_phrasing():
    body = "Saw the article on your voice AI for blue-collar hiring. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("refers to the source" in e for e in errs)

def test_rejects_publication_name_in_body():
    body = ("The Economic Times wrote about your blue-collar hiring voice AI at Vahan. "
            "Would you take an intern?")
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("publication" in e for e in errs)

def test_loophole_only_overlap_is_publication_name_fails_grounding():
    # D29: naming the outlet must not by itself satisfy grounding.
    body = "Economic Times. Vahan is great. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("publication" in e or "shares no distinctive term" in e for e in errs)

def test_rejects_when_no_distinctive_term_shared():
    body = "Your product looks good at Vahan. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("shares no distinctive term" in e for e in errs)

def test_rejects_missing_company_name():
    body = ("You're matching blue-collar workers to jobs over voice. "
            "Would you take an intern?")
    errs = validate_grounding(d(body=body), NEWS, "Vahan", "x", CLAIMS)
    assert any("does not name the company" in e for e in errs)

def test_rejects_non_intern_appropriate_job_posting():
    posting = SourceRow(kind="job_posting", text="Senior Engineer",
                        publication=None, intern_appropriate=False)
    body = "Your Senior Engineer opening at Vahan caught my eye. Would you take an intern?"
    errs = validate_grounding(d(body=body, stype="job_posting"), posting, "Vahan", "x", CLAIMS)
    assert any("not intern-appropriate" in e for e in errs)

def test_rejects_hook_that_parrots_the_one_liner():
    one_liner = "Recruitment platform for blue-collar workers using voice"
    body = one_liner + " at Vahan. Would you take an intern?"
    errs = validate_grounding(d(body=body), NEWS, "Vahan", one_liner, CLAIMS)
    assert any("one_liner" in e for e in errs)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/domain/test_validation_grounding.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_grounding'`

- [ ] **Step 3: Append the grounding validator to `validation.py`**

```python
from dataclasses import dataclass

SOURCE_REFERENCE_PHRASES = [
    "saw the", "read the", "read about", "the article", "the piece",
    "coverage", "featured in", "in the news", "press release", "came across",
]
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "at", "with",
    "is", "are", "was", "were", "that", "this", "it", "its", "your", "you",
    "their", "from", "by", "as", "be", "has", "have", "we", "our", "i",
}


@dataclass(frozen=True)
class SourceRow:
    kind: str
    text: str
    publication: str | None
    intern_appropriate: bool | None


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z0-9']+", text.lower())
            if w not in _STOPWORDS and len(w) > 2}


def validate_grounding(draft, source, company_name, one_liner, claims) -> list[str]:
    errs: list[str] = []
    body = draft.body
    low = body.lower()

    if source is None or draft.research_source_id is None:
        return ["research source does not resolve to a real row"]

    for phrase in SOURCE_REFERENCE_PHRASES:
        if phrase in low:
            errs.append(f"refers to the source as a source: {phrase!r}")

    pub_terms: set[str] = set()
    if source.publication:
        pub_terms = _terms(source.publication)
        if source.publication.lower() in low:
            errs.append("publication name appears in the body")

    substantive = _terms(source.text) - pub_terms
    shared = substantive & _terms(body)
    if not shared:
        errs.append("hook shares no distinctive term with the cited source")

    if company_name and company_name.lower() not in low:
        errs.append("does not name the company")

    if source.kind == "job_posting" and source.intern_appropriate is False:
        errs.append("cited job posting is not intern-appropriate")

    if one_liner:
        ol = _terms(one_liner)
        if ol and len(ol & _terms(body)) / len(ol) > 0.6:
            errs.append("hook overlaps more than 60% with one_liner")

    claim_terms: set[str] = set()
    for c in claims:
        claim_terms |= _terms(c.text)
    return errs


def validate_draft(draft, source, company_name, one_liner, claims,
                   previous_body=None) -> list[str]:
    return (validate_style(draft, previous_body)
            + validate_grounding(draft, source, company_name, one_liner, claims))
```

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/domain/ -v`
Expected: PASS — all domain tests green

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/validation.py backend/tests/domain/test_validation_grounding.py
git commit -m "feat: grounding validator closing the publication-name loophole (D29)"
```

---

## Task 8: Ports and in-memory fakes

**Files:**
- Create: `backend/app/ports/__init__.py`, `backend/tests/fakes.py`
- Test: `backend/tests/test_fakes_satisfy_ports.py`

**Interfaces:**
- Produces the Protocols every later task depends on. Exact signatures:

```python
class YCDirectory(Protocol):
    def fetch_companies(self) -> list[dict]: ...

class CompanyPageSource(Protocol):
    def fetch_page(self, slug: str) -> dict: ...   # returns the parsed `props`

class EmailVerifier(Protocol):
    def verify(self, address: str) -> str: ...     # valid|invalid|catch_all|unknown
    def remaining_quota(self) -> int | None: ...

class Drafter(Protocol):
    def draft(self, prompt_context: dict) -> dict: ...   # {subject, body, research_source_type, research_source_id}

class ResumeParser(Protocol):
    def parse(self, raw_text: str) -> list[dict]: ...    # [{kind, text, source_span}]

class MailSender(Protocol):
    def send(self, to: str, subject: str, body: str,
             attachment_path: str | None) -> tuple[str, str]: ...  # (message_id, thread_id)

class Clock(Protocol):
    def now(self) -> datetime: ...
```

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_fakes_satisfy_ports.py
from app import ports
from tests import fakes

def test_fakes_are_structural_subtypes():
    v: ports.EmailVerifier = fakes.FakeVerifier({})
    d: ports.Drafter = fakes.FakeDrafter()
    s: ports.MailSender = fakes.FakeSender()
    c: ports.Clock = fakes.FrozenClock()
    assert v and d and s and c

def test_fake_verifier_records_calls_and_respects_quota():
    v = fakes.FakeVerifier({"a@x.com": "valid"}, quota=1)
    assert v.verify("a@x.com") == "valid"
    assert v.calls == ["a@x.com"]
    assert v.remaining_quota() == 0

def test_fake_sender_returns_ids_and_records_sends():
    s = fakes.FakeSender()
    mid, tid = s.send("a@x.com", "subject here now", "body", None)
    assert mid and tid
    assert s.sent[0]["to"] == "a@x.com"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/test_fakes_satisfy_ports.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ports'`

- [ ] **Step 3: Write `app/ports/__init__.py`**

Use the exact Protocol definitions from the Interfaces block above, each decorated `@runtime_checkable` and importing `Protocol` from `typing`.

- [ ] **Step 4: Write `tests/fakes.py`**

```python
from datetime import datetime, timezone


class FakeVerifier:
    def __init__(self, verdicts: dict[str, str], quota: int | None = None):
        self._verdicts, self._quota, self.calls = verdicts, quota, []

    def verify(self, address: str) -> str:
        if self._quota is not None:
            if self._quota <= 0:
                raise RuntimeError("quota exhausted")
            self._quota -= 1
        self.calls.append(address)
        return self._verdicts.get(address, "invalid")

    def remaining_quota(self) -> int | None:
        return self._quota


class FakeDrafter:
    def __init__(self, responses: list[dict] | None = None):
        self._responses = responses or []
        self.calls: list[dict] = []

    def draft(self, prompt_context: dict) -> dict:
        self.calls.append(prompt_context)
        if self._responses:
            return self._responses.pop(0)
        return {"subject": "Backend intern at Acme", "body": "Would you take an intern?",
                "research_source_type": "news", "research_source_id": 1}


class FakeSender:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, body, attachment_path):
        self.sent.append({"to": to, "subject": subject, "body": body})
        n = len(self.sent)
        return (f"msg-{n}", f"thread-{n}")


class FrozenClock:
    def __init__(self, at: datetime | None = None):
        self._at = at or datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._at
```

- [ ] **Step 5: Run the tests — they should pass**

Run: `cd backend && pytest tests/test_fakes_satisfy_ports.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/ports backend/tests/fakes.py backend/tests/test_fakes_satisfy_ports.py
git commit -m "feat: port protocols and in-memory fakes for offline testing"
```

---

## Task 9: Persistence — models, migration and repositories

**Files:**
- Create: `backend/app/adapters/db/models.py`, `backend/app/adapters/db/repositories.py`
- Create: `backend/alembic/versions/0001_initial.py` (generated)
- Test: `backend/tests/adapters/test_repositories.py` (marked `integration`)

**Interfaces:**
- Consumes: `OutreachState`.
- Produces: SQLAlchemy models for `companies`, `job_postings`, `founders`, `company_news`, `company_launches`, `email_candidates`, `outreach`, `messages`, `resume_profile`, `settings` (spec §7, minus `outcomes` and `messages.critic_*` which are Phase 2). Plus `CompanyRepo`, `FounderRepo`, `OutreachRepo`, `MessageRepo` with `upsert`/`get`/`list_by_state` methods.

Two constraints are load-bearing and must be in the migration:
- `UniqueConstraint("outreach_id", "sequence_step")` on outbound `messages` — makes a double-send impossible (spec §13).
- `CheckConstraint("sent_at IS NULL OR approved_at IS NOT NULL")` on `messages` — an unapproved message can never be marked sent (D5).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/adapters/test_repositories.py
import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

def test_duplicate_outbound_message_is_rejected(session, an_outreach):
    from app.adapters.db.models import Message
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b"))
    session.commit()
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s2", body="b2"))
    with pytest.raises(IntegrityError):
        session.commit()

def test_sent_without_approved_is_rejected(session, an_outreach):
    from app.adapters.db.models import Message
    from datetime import datetime, timezone
    session.add(Message(outreach_id=an_outreach.id, sequence_step=0,
                        direction="outbound", subject="s", body="b",
                        sent_at=datetime.now(timezone.utc), approved_at=None))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/adapters/test_repositories.py -m integration -v`
Expected: FAIL — models do not exist yet.

- [ ] **Step 3: Write the models**

Write `app/adapters/db/models.py` using SQLAlchemy 2.0 `DeclarativeBase` and `Mapped[...]` annotations, one class per table listed in the Interfaces block, with the two constraints above declared in `__table_args__` on `Message`.

- [ ] **Step 4: Generate and review the migration**

Run: `cd backend && docker compose up -d && alembic revision --autogenerate -m "initial schema"`
Open the generated file and confirm both the unique constraint and the check constraint are present. Autogenerate sometimes omits `CheckConstraint` — add it by hand if missing.

- [ ] **Step 5: Apply and test**

Run: `cd backend && alembic upgrade head && pytest tests/adapters/test_repositories.py -m integration -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Confirm the offline suite still passes**

Run: `cd backend && pytest`
Expected: PASS, integration tests deselected, no network used.

- [ ] **Step 7: Commit**

```bash
git add backend/app/adapters/db backend/alembic/versions backend/tests/adapters
git commit -m "feat: schema with double-send and unapproved-send constraints"
```

---

## Task 10: YC company sync

**Files:**
- Create: `backend/app/adapters/yc_oss.py`, `backend/app/application/sync_companies.py`
- Create: `backend/app/cli.py`
- Test: `backend/tests/application/test_sync_companies.py`

**Interfaces:**
- Consumes: `YCDirectory`, `CompanyRepo`, `is_target_company`.
- Produces: `SyncCompanies(directory, repo).run() -> SyncResult` with `fetched: int`, `matched: int`, `upserted: int`. CLI: `python -m app.cli sync`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_sync_companies.py
from app.application.sync_companies import SyncCompanies

class FakeDirectory:
    def __init__(self, rows): self._rows = rows
    def fetch_companies(self): return self._rows

class FakeRepo:
    def __init__(self): self.rows = {}
    def upsert(self, company): self.rows[company.yc_id] = company; return company

INDIA = {"id": 1, "slug": "swipe", "name": "Swipe", "website": "https://getswipe.in",
         "regions": ["India"], "all_locations": "Hyderabad, India", "status": "Active",
         "team_size": 60, "isHiring": False, "one_liner": "AI accountant",
         "long_description": "x" * 200, "batch": "Summer 2021"}
US = {**INDIA, "id": 2, "slug": "other", "regions": ["United States of America"],
      "all_locations": "Boston, MA, USA"}
TOO_BIG = {**INDIA, "id": 3, "slug": "big", "team_size": 900}

def test_only_target_companies_are_upserted():
    repo = FakeRepo()
    result = SyncCompanies(FakeDirectory([INDIA, US, TOO_BIG]), repo).run()
    assert result.fetched == 3
    assert result.matched == 1
    assert list(repo.rows) == [1]

def test_domain_is_derived_from_website():
    repo = FakeRepo()
    SyncCompanies(FakeDirectory([INDIA]), repo).run()
    assert repo.rows[1].domain == "getswipe.in"

def test_rerun_is_idempotent():
    repo = FakeRepo()
    d = FakeDirectory([INDIA])
    SyncCompanies(d, repo).run()
    SyncCompanies(d, repo).run()
    assert len(repo.rows) == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/application/test_sync_companies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.application.sync_companies'`

- [ ] **Step 3: Write the use case**

```python
# backend/app/application/sync_companies.py
from dataclasses import dataclass
from urllib.parse import urlparse

from app.domain.entities import Company
from app.domain.targeting import is_target_company


@dataclass
class SyncResult:
    fetched: int
    matched: int
    upserted: int


def derive_domain(website: str | None) -> str | None:
    if not website:
        return None
    netloc = urlparse(website if "//" in website else f"//{website}").netloc
    return netloc.removeprefix("www.").lower() or None


class SyncCompanies:
    def __init__(self, directory, repo):
        self._directory, self._repo = directory, repo

    def run(self) -> SyncResult:
        rows = self._directory.fetch_companies()
        matched = [r for r in rows if is_target_company(r)]
        for raw in matched:
            self._repo.upsert(Company(
                yc_id=raw["id"], slug=raw["slug"], name=raw["name"],
                website=raw.get("website"), domain=derive_domain(raw.get("website")),
                batch=raw.get("batch"), team_size=raw.get("team_size"),
                status=raw.get("status"), is_hiring=bool(raw.get("isHiring")),
                one_liner=raw.get("one_liner"),
                long_description=raw.get("long_description"),
            ))
        return SyncResult(len(rows), len(matched), len(matched))
```

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/application/test_sync_companies.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the adapter and CLI command**

`app/adapters/yc_oss.py` fetches `https://yc-oss.github.io/api/companies/all.json` with httpx (30s timeout) and returns the parsed list. `app/cli.py` gains a `sync` command wiring the real adapter and repo.

- [ ] **Step 6: Run it for real and check the count**

Run: `cd backend && python -m app.cli sync`
Expected: `fetched 6200+, matched 144` — if `matched` is not close to 144, the targeting filter or the upstream data has changed; investigate before continuing.

- [ ] **Step 7: Commit**

```bash
git add backend/app/adapters/yc_oss.py backend/app/application/sync_companies.py backend/app/cli.py backend/tests/application
git commit -m "feat: sync the 144 target companies from the yc-oss dataset"
```

---

## Task 11: Founder and research scraping

**Files:**
- Create: `backend/app/adapters/yc_page.py`, `backend/app/application/scrape_company_page.py`
- Test: `backend/tests/adapters/test_yc_page.py`, `backend/tests/application/test_scrape_company_page.py`
- Fixture: `backend/tests/fixtures/vahan_page.html` (capture a real page once, commit it)

**Interfaces:**
- Consumes: `CompanyPageSource`, repos.
- Produces: `parse_company_page(html: str) -> dict` with keys `founders`, `job_postings`, `news`, `launches`. `ScrapeCompanyPage(source, repos).run(company) -> ScrapeResult`. CLI: `python -m app.cli scrape`.

Extract the `data-page` attribute, HTML-unescape it, JSON-parse it, then read `props.company.founders[]`, `props.jobPostings[]`, `props.newsItems[]`, `props.launches[]`. Persist each posting's `type` into `employment_type` (D23 depends on it). Parse `mailto:`-style addresses out of launch bodies into `published_email` (D18). Rate limit to 1 request/second.

- [ ] **Step 1: Capture the fixture**

```bash
cd backend && mkdir -p tests/fixtures && \
curl -sL -A "Mozilla/5.0" https://www.ycombinator.com/companies/vahan \
  -o tests/fixtures/vahan_page.html && \
wc -c tests/fixtures/vahan_page.html
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/adapters/test_yc_page.py
from pathlib import Path
from app.adapters.yc_page import parse_company_page

HTML = (Path(__file__).parent.parent / "fixtures" / "vahan_page.html").read_text(encoding="utf-8")

def test_extracts_founders_with_names_and_linkedin():
    parsed = parse_company_page(HTML)
    assert parsed["founders"], "expected at least one founder"
    f = parsed["founders"][0]
    assert f["full_name"] and f["linkedin_url"]

def test_extracts_news_items_with_source_and_url():
    parsed = parse_company_page(HTML)
    assert parsed["news"], "Vahan had news items when the fixture was captured"
    assert parsed["news"][0]["url"].startswith("http")

def test_extracts_job_postings_with_employment_type():
    parsed = parse_company_page(HTML)
    for p in parsed["job_postings"]:
        assert "employment_type" in p

def test_extracts_published_email_from_launch_body():
    body = "reach me at [madhav@vahan.ai](mailto:madhav@vahan.ai)"
    from app.adapters.yc_page import extract_published_email
    assert extract_published_email(body) == "madhav@vahan.ai"

def test_returns_empty_lists_when_arrays_absent():
    minimal = '<div data-page="{&quot;props&quot;:{&quot;company&quot;:{&quot;founders&quot;:[]}}}"></div>'
    parsed = parse_company_page(minimal)
    assert parsed == {"founders": [], "job_postings": [], "news": [], "launches": []}
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd backend && pytest tests/adapters/test_yc_page.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.yc_page'`

- [ ] **Step 4: Write the parser**

```python
# backend/app/adapters/yc_page.py
import html as html_lib
import json
import re

_DATA_PAGE = re.compile(r'data-page="([^"]+)"')
_MAILTO = re.compile(r"mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def extract_published_email(body: str | None) -> str | None:
    if not body:
        return None
    m = _MAILTO.search(body)
    return m.group(1) if m else None


def parse_company_page(page_html: str) -> dict:
    m = _DATA_PAGE.search(page_html)
    if not m:
        raise ValueError("no data-page attribute found")
    props = json.loads(html_lib.unescape(m.group(1)))["props"]
    company = props.get("company") or {}

    return {
        "founders": [
            {"yc_user_id": f.get("user_id"), "full_name": f.get("full_name"),
             "title": f.get("title"), "linkedin_url": f.get("linkedin_url"),
             "twitter_url": f.get("twitter_url"), "bio": f.get("founder_bio")}
            for f in (company.get("founders") or [])
        ],
        "job_postings": [
            {"title": j.get("title"), "employment_type": j.get("type"),
             "min_experience": j.get("minExperience"), "location": j.get("location"),
             "url": j.get("url")}
            for j in (props.get("jobPostings") or [])
        ],
        "news": [
            {"title": n.get("title"), "url": n.get("url"),
             "source": (n.get("title") or "").rsplit(" - ", 1)[-1] if " - " in (n.get("title") or "") else None,
             "published_at": n.get("date")}
            for n in (props.get("newsItems") or [])
        ],
        "launches": [
            {"title": l.get("title"), "body": l.get("body"),
             "published_email": extract_published_email(l.get("body"))}
            for l in (props.get("launches") or [])
        ],
    }
```

- [ ] **Step 5: Run the tests — they should pass**

Run: `cd backend && pytest tests/adapters/test_yc_page.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Write the use case and CLI command**

`ScrapeCompanyPage` iterates companies needing a scrape, calls the source, upserts founders / postings / news / launches, and creates an `Outreach` row in `NEW` for each new founder. Sleep 1s between requests. A parse failure records the error against that company and continues.

- [ ] **Step 7: Run it for real**

Run: `cd backend && python -m app.cli scrape`
Expected: roughly 269 founders across 144 companies. Takes ~3 minutes at 1 req/s.

- [ ] **Step 8: Commit**

```bash
git add backend/app/adapters/yc_page.py backend/app/application/scrape_company_page.py backend/tests
git commit -m "feat: scrape founders, news, launches and postings from one page fetch"
```

---

## Task 12: Email resolution

**Files:**
- Create: `backend/app/adapters/email_verifier.py`, `backend/app/application/resolve_emails.py`
- Test: `backend/tests/application/test_resolve_emails.py`

**Interfaces:**
- Consumes: `EmailVerifier`, repos, `generate_candidates`.
- Produces: `ResolveEmails(verifier, repos).run(limit: int | None) -> ResolveResult` with `resolved`, `manual`, `verifications_used`. CLI: `python -m app.cli resolve --limit 50`.

Order (spec §9): a `published_email` from a launch post short-circuits everything (D18) — attribute it only when the company has exactly one founder or the founder's name appears near the address in the body. Otherwise verify candidates in priority order, stopping at the first `valid`. All `invalid` → `NEEDS_MANUAL_EMAIL`. Any `catch_all` → `NEEDS_MANUAL_EMAIL`, never a guess. Quota exhaustion pauses the stage rather than counting as a failure.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_resolve_emails.py
from app.application.resolve_emails import ResolveEmails
from app.domain.states import OutreachState as S
from tests.fakes import FakeVerifier

def test_published_email_short_circuits_without_verification(repos, founder_with_launch_email):
    v = FakeVerifier({})
    result = ResolveEmails(v, repos).run()
    assert v.calls == []                      # no verification spent
    assert result.verifications_used == 0
    assert repos.outreach.get(founder_with_launch_email.id).state == S.READY_TO_DRAFT

def test_stops_at_first_valid_candidate(repos, a_founder):
    v = FakeVerifier({"pratish@onefin.com": "invalid",
                      "pratish.gandhi@onefin.com": "valid"})
    ResolveEmails(v, repos).run()
    assert v.calls == ["pratish@onefin.com", "pratish.gandhi@onefin.com"]
    assert repos.outreach.get(a_founder.id).resolved_email == "pratish.gandhi@onefin.com"

def test_all_invalid_routes_to_manual(repos, a_founder):
    ResolveEmails(FakeVerifier({}), repos).run()
    assert repos.outreach.get(a_founder.id).state == S.NEEDS_MANUAL_EMAIL

def test_catch_all_never_guesses(repos, a_founder):
    v = FakeVerifier({"pratish@onefin.com": "catch_all"})
    ResolveEmails(v, repos).run()
    o = repos.outreach.get(a_founder.id)
    assert o.state == S.NEEDS_MANUAL_EMAIL
    assert o.resolved_email is None

def test_quota_exhaustion_pauses_rather_than_failing(repos, two_founders):
    v = FakeVerifier({}, quota=1)
    result = ResolveEmails(v, repos).run()
    assert result.paused_on_quota is True
    assert all(o.state != S.FAILED for o in repos.outreach.all())
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/application/test_resolve_emails.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the use case**

Implement the ordering above. Persist every verdict as an `email_candidates` row (including `published`) so a re-run never re-verifies an address already judged.

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/application/test_resolve_emails.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the MyEmailVerifier adapter (D30)**

`GET https://client.myemailverifier.com/verifier/validate_single/{email}/{apikey}`.
Verified live 2026-09-04: returns JSON, and an invalid key yields HTTP 401
`{"status":false,"message":"Invalid API Key"}`. A successful response carries
`Status` (`Valid` | `Invalid` | `Catch-all` | `Unknown`), plus `catch_all`,
`Disposable_Domain`, `Role_Based`, `Diagnosis`. Map `Status` case-insensitively
onto the domain verdicts `valid` / `invalid` / `catch_all` / `unknown`, treating
any unrecognised value as `unknown` rather than guessing. Raise a distinct
`QuotaExhausted` exception when the daily 100-credit free allowance is spent so
the use case can pause rather than record failures. Single-verification rate
limit is 30/min — sleep between calls to stay under it.

The URL embeds the API key in the path, so it must never be logged. Log the
address and the verdict, never the full URL.

- [ ] **Step 6: Run a small real batch**

Run: `cd backend && python -m app.cli resolve --limit 20`
Expected: roughly 14 of 20 resolve to a `valid` address. Check `verifications_used` is close to 2.5 per founder — materially higher means the pattern ordering needs revisiting. Note the free tier is 100/day (D30), so a 20-founder batch costs about half a day's allowance.

- [ ] **Step 7: Commit**

```bash
git add backend/app/adapters/email_verifier.py backend/app/application/resolve_emails.py backend/tests
git commit -m "feat: email resolution with published-email short circuit and early exit"
```

---

## Task 13: Resume parsing

**Files:**
- Create: `backend/app/adapters/gemini.py` (first half), `backend/app/application/parse_resume.py`
- Test: `backend/tests/application/test_parse_resume.py`

**Interfaces:**
- Consumes: `ResumeParser`, `ResumeProfileRepo`.
- Produces: `ParseResume(parser, repo).run(raw_text: str) -> list[ResumeClaim]`. CLI: `python -m app.cli parse-resume path/to/resume.pdf`.

Claims are the only things the drafter may cite (spec §10). Each claim carries the verbatim `source_span` it came from, so a claim can always be traced back to the resume.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_parse_resume.py
from app.application.parse_resume import ParseResume

class FakeParser:
    def parse(self, raw_text):
        return [{"kind": "project", "text": "built a scheduler in Python",
                 "source_span": "Built a scheduler in Python, cut wait 50%"}]

class FakeProfileRepo:
    def __init__(self): self.saved = None
    def save(self, raw_text, claims): self.saved = (raw_text, claims)

def test_claims_are_persisted_with_source_spans():
    repo = FakeProfileRepo()
    claims = ParseResume(FakeParser(), repo).run("resume text")
    assert claims[0].source_span
    assert repo.saved[0] == "resume text"

def test_rejects_empty_resume():
    import pytest
    with pytest.raises(ValueError):
        ParseResume(FakeParser(), FakeProfileRepo()).run("   ")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/application/test_parse_resume.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the use case, then the Gemini adapter**

The adapter calls Gemini with a `responseSchema` returning a list of `{kind, text, source_span}`. `kind` is one of `skill|project|metric|role|education`. Model id comes from `settings.gemini_model` — never hardcoded (D13).

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/application/test_parse_resume.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Parse the real resume and read the claims**

Run: `cd backend && python -m app.cli parse-resume ~/resume.pdf && python -m app.cli show-claims`
Read every claim. These are what the emails will assert about you — a wrong claim here becomes a wrong claim in 269 emails.

- [ ] **Step 6: Commit**

```bash
git add backend/app/adapters/gemini.py backend/app/application/parse_resume.py backend/tests
git commit -m "feat: parse resume into structured citable claims"
```

---

## Task 14: Drafting

**Files:**
- Modify: `backend/app/adapters/gemini.py` (add `GeminiDrafter`)
- Create: `backend/app/application/draft_outreach.py`
- Test: `backend/tests/application/test_draft_outreach.py`

**Interfaces:**
- Consumes: `Drafter`, repos, `validate_draft`, `is_intern_appropriate`.
- Produces: `DraftOutreach(drafter, repos).run(limit) -> DraftResult` with `drafted`, `manual_write`, `retried`. CLI: `python -m app.cli draft --limit 20`.

Source priority (D17): news → launch → intern-appropriate posting → `long_description`. A non-intern-appropriate posting is skipped, never used as a fallback (D23). On validation failure, retry **once** with the errors appended; a second failure moves the row to `PENDING_APPROVAL` flagged *needs manual write*, never silently dropped.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_draft_outreach.py
from app.application.draft_outreach import DraftOutreach
from app.domain.states import OutreachState as S
from tests.fakes import FakeDrafter

GOOD = {"subject": "Backend intern at Vahan",
        "body": ("You're matching blue-collar workers to jobs over voice at Vahan. "
                 "That routing problem is what I keep thinking about. "
                 "I built a scheduler in Python. Would you take an intern?"),
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/application/test_draft_outreach.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the use case**

Build the source list in priority order, filtering postings through `is_intern_appropriate`. Call the drafter, validate, retry once on failure, persist the `messages` row with `research_source_type` / `research_source_id`, and move the outreach to `PENDING_APPROVAL`.

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/application/test_draft_outreach.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write `GeminiDrafter` with the response schema**

Schema: `{subject: string, body: string, research_source_type: enum, research_source_id: integer}`. The prompt states the three-part structure, the ~90 word target, and that the source is how the sender learned something and must never appear in the body (D29).

- [ ] **Step 6: Draft five real emails and read them**

Run: `cd backend && python -m app.cli draft --limit 5 && python -m app.cli review --dry-run`
Read all five. This is the first point where the actual output quality is visible — if they read like templates, fix the prompt now, before drafting 269.

- [ ] **Step 7: Commit**

```bash
git add backend/app/adapters/gemini.py backend/app/application/draft_outreach.py backend/tests
git commit -m "feat: draft internship enquiries with grounded sources and one retry"
```

---

## Task 15: Gmail sending

**Files:**
- Create: `backend/app/adapters/gmail.py`, `backend/app/application/send_approved.py`
- Test: `backend/tests/application/test_send_approved.py`

**Interfaces:**
- Consumes: `MailSender`, `Clock`, repos.
- Produces: `SendApproved(sender, clock, repos).run(cap: int) -> SendResult` with `sent`, `skipped_cap`. CLI: `python -m app.cli send`.

**Reserve before sending (spec §13).** The order is not optional:
1. Insert the outbound `messages` row with `sent_at` null, in its own committed transaction. The unique constraint on `(outreach_id, sequence_step)` means a retry after a crash fails here and stops.
2. Call Gmail.
3. Update the reserved row with `gmail_message_id`, `gmail_thread_id`, `sent_at`, and advance the state.

Sending before reserving is a real double-send bug: if Gmail succeeds and the transaction then fails, the retry finds nothing to conflict with and emails the founder twice.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/application/test_send_approved.py
import pytest
from app.application.send_approved import SendApproved
from app.domain.states import OutreachState as S
from tests.fakes import FakeSender, FrozenClock

def test_only_approved_messages_are_sent(repos, pending_and_approved):
    s = FakeSender()
    SendApproved(s, FrozenClock(), repos).run(cap=10)
    assert len(s.sent) == 1
    assert s.sent[0]["to"] == "approved@example.com"

def test_daily_cap_is_respected(repos, five_approved):
    s = FakeSender()
    result = SendApproved(s, FrozenClock(), repos).run(cap=2)
    assert len(s.sent) == 2
    assert result.skipped_cap == 3

def test_crash_after_gmail_call_does_not_double_send(repos, one_approved):
    class ExplodingSender(FakeSender):
        def send(self, *a, **kw):
            super().send(*a, **kw)
            raise RuntimeError("crashed after the API call")

    s = ExplodingSender()
    with pytest.raises(RuntimeError):
        SendApproved(s, FrozenClock(), repos).run(cap=10)

    # The reservation survives, so the retry cannot send a second copy.
    s2 = FakeSender()
    SendApproved(s2, FrozenClock(), repos).run(cap=10)
    assert s2.sent == []

def test_state_advances_to_sent(repos, one_approved):
    SendApproved(FakeSender(), FrozenClock(), repos).run(cap=10)
    assert repos.outreach.get(one_approved.id).state == S.SENT
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/application/test_send_approved.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the use case with reserve-then-send ordering**

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/application/test_send_approved.py -v`
Expected: PASS (4 tests) — the third test is the one that matters most.

- [ ] **Step 5: Write the Gmail adapter**

OAuth via `google-auth-oauthlib` with scope `https://www.googleapis.com/auth/gmail.send`. Build a MIME message with the resume attached, base64url-encode, and POST to `users.messages.send`. Store the refresh token in Postgres, not a file. Plain text only — no HTML part, no tracking pixel (D10).

- [ ] **Step 6: Send exactly one real email to yourself**

Run: `cd backend && python -m app.cli send --to-self --limit 1`
Check the received mail: the attachment opens, the body is plain text, the signature is right, and it does not land in spam.

- [ ] **Step 7: Commit**

```bash
git add backend/app/adapters/gmail.py backend/app/application/send_approved.py backend/tests
git commit -m "feat: gmail sending with reserve-before-send double-send protection"
```

---

## Task 16: Terminal review loop and end-to-end run

**Files:**
- Modify: `backend/app/cli.py`
- Test: `backend/tests/test_cli_review.py`

**Interfaces:**
- Consumes: every use case above.
- Produces: `python -m app.cli review` — an interactive loop over `PENDING_APPROVAL` rows showing the draft, the company, the founder, and the cited source row, with keys to approve / edit / reject / skip.

The cited source is shown next to the draft so a claim can be checked at a glance rather than by re-researching the company.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_cli_review.py
from app.cli import render_review_screen

def test_review_screen_shows_the_cited_source(a_pending_message, a_news_source):
    out = render_review_screen(a_pending_message, a_news_source)
    assert a_news_source.title[:20] in out
    assert "SOURCE" in out

def test_review_screen_shows_word_count(a_pending_message, a_news_source):
    out = render_review_screen(a_pending_message, a_news_source)
    assert "words" in out.lower()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/test_cli_review.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_review_screen'`

- [ ] **Step 3: Write `render_review_screen` and the interactive loop**

Keep rendering pure (a function returning a string) so it stays testable; the loop handles input separately.

- [ ] **Step 4: Run the tests — they should pass**

Run: `cd backend && pytest tests/test_cli_review.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Full offline suite must be green**

Run: `cd backend && pytest`
Expected: PASS with no env vars and no network. This is the Phase 1 definition of done.

- [ ] **Step 6: End-to-end dry run**

```bash
cd backend && python -m app.cli sync && python -m app.cli scrape \
  && python -m app.cli resolve && python -m app.cli draft --limit 20 \
  && python -m app.cli review
```

Expected: ~144 companies, ~269 founders, ~70% with a resolved address, 20 drafts to review.

- [ ] **Step 7: Commit**

```bash
git add backend/app/cli.py backend/tests/test_cli_review.py
git commit -m "feat: terminal review loop showing the cited source beside each draft"
```

---

## Self-Review

**Spec coverage.** Phase 1 covers §8 stages 1, 2, 3, 4, 5, 6 and 7; §9 email resolution including D18; §10 drafting and both halves of the validator including D23, D27, D28, D29; §13 idempotency and the approval constraint; §15 testing. Deliberately deferred and tracked in Phase 2: §8 stages 8-9 (reply polling, follow-ups), §11 reply classification, §12 follow-up policy, §16 frontend, the D22 critic, and the D20 worker processes.

**Placeholder scan.** No TBDs. Tasks 9, 11 (step 6), 13, 14 (steps 3 and 5) and 15 (steps 3 and 5) describe implementations in prose rather than full code — these are mechanical given the exact interfaces, constraints and response schemas stated in the same task, but they are the least prescriptive steps in the plan and the most likely to need judgement.

**Type consistency.** `Draft`, `ResumeClaim` and `SourceRow` field names are used identically in Tasks 5, 6, 7 and 14. `OutreachState` members match between Task 5 and their uses in Tasks 12, 14 and 15. `generate_candidates` returns `Candidate(address, pattern, priority)` in Task 3 and is consumed with those names in Task 12. Port method names in Task 8 match every adapter and fake.

**Known gap.** `validate_grounding` computes `claim_terms` but does not yet use it — the "asserts a skill absent from `resume_profile.claims`" rule needs a similarity threshold that is genuinely hard to get right without seeing real drafts. Task 14 step 6 is where that becomes visible; tighten it then, with real output in hand, rather than guessing now.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-04-yc-auto-apply-phase-1.md`.
