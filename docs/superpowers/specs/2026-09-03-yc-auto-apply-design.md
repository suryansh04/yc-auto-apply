# YC Auto Apply — Design Spec

**Date:** 2026-09-03
**Status:** Approved, ready for implementation planning
**Author:** Suryansh Singh (suryanshsingh0987@gmail.com)

---

## 1. Problem and Goal

A single job seeker wants to reach the founders of medium-sized Indian
Y Combinator startups with a personalised cold email that attaches a resume and
argues, specifically and in a human voice, why the sender is a good fit — then
track every send, reply and outcome in one dashboard.

**Goal:** an end-to-end system that discovers the target companies, finds their
founders, resolves founder email addresses, drafts a tailored email per founder,
routes every draft through human approval, sends from the user's own Gmail,
follows up twice when there is no reply, and classifies every reply into an
outcome shown on a dashboard.

---

## 2. Scope

**In scope**

- Nightly sync of YC companies filtered to India, active, team size 11–200.
- Scraping founder name, title, LinkedIn, bio and open job postings from YC
  company pages.
- Founder email resolution by pattern generation plus third-party verification.
- Resume parsing into structured, citable claims.
- LLM drafting with a deterministic anti-slop validator.
- A human approval queue. Nothing is sent without explicit approval.
- Sending via the user's Gmail account with a daily cap.
- Two follow-ups per founder when there is no human reply.
- Reply polling and LLM classification into a fixed outcome taxonomy.
- A React dashboard: pipeline health, approval queue, contacts, per-thread timeline.

**Out of scope**

- Multi-user accounts, signup, billing, teams. This is a single-user system.
- Any job source other than the YC directory.
- LinkedIn automation, connection requests or InMail.
- Open tracking pixels, link shorteners and click tracking. Deliberately
  excluded — they are the strongest spam signals in cold email and they are the
  main reason personal-domain cold mail lands in spam.
- Calendar scheduling, ATS integration, CRM export.

---

## 3. Research Findings That Constrain the Design

These were measured on 2026-09-03, not assumed. They are the factual basis for
several decisions below.

**Company data is free and structured.** `https://yc-oss.github.io/api/companies/all.json`
publishes 6,200 YC companies, updated daily, with `team_size`, `regions[]`,
`all_locations`, `batch`, `status`, `stage`, `isHiring`, `website`, `one_liner`
and `long_description`.

**The target universe is small and fixed.** Filtering that dataset:

| Filter | Count |
| --- | --- |
| All launched YC companies | 6,200 |
| Tagged India | 223 |
| India + status Active | 158 |
| India + Active + team_size 11–200 (the target band) | **96** |
| ...of which currently hiring | 34 |
| ...with a usable website | 96 |

**Founder data is scrapeable; founder emails are not.** YC company pages embed
an Inertia.js payload in a `data-page="..."` HTML attribute. Parsing it yields a
`props.company.founders[]` array. Measured across a 14-company sample
(28 founders):

| Field | Coverage |
| --- | --- |
| `full_name`, `title` | 28/28 |
| `linkedin_url` | 28/28 |
| `founder_bio` | 13/28 |
| `twitter_url` | 9/28 |
| `has_email` | 28/28 — **a boolean flag only; the address is not in the payload** |

The same payload carries `props.userCanRequestEmails: false`. YC gates actual
founder addresses behind a logged-in Bookface/investor account. Scraping
therefore supplies the entire targeting and personalisation dataset — roughly
**192 founders across the 96 companies** — but cannot supply the address.

**Consequence:** the only step that needs a paid third party is
`exact full name + company domain -> verified email address`.

---

## 4. Decisions and Rationale

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Companies come from `yc-oss` JSON, not from scraping the directory | Free, daily-updated, structured, and avoids hammering YC's search backend |
| D2 | Founders come from parsing the `data-page` payload on YC company pages | The only free source of exact founder names and titles; 100% LinkedIn coverage in sampling |
| D3 | Email resolution = in-house pattern generation + Reoon Email Verifier | Reoon's free tier is 600 verifications/month for life; early-exit ordering keeps a full run at ~480 checks, inside the free tier |
| D4 | Send through the user's own Gmail via OAuth | Free, an established account has real sending reputation, and Gmail thread IDs give reply tracking with no extra infrastructure |
| D5 | Every outbound message passes a human approval queue | At 96 companies the user can review every draft; this is the strongest available defence against low-quality LLM output |
| D6 | Two follow-ups, at +5 and +12 days, cancelled by any human reply | Most cold-outreach replies come from follow-ups, not the first email |
| D7 | Modular monolith: FastAPI app + worker + Postgres, DB-as-queue | Production-shaped without Redis or Celery; `SELECT ... FOR UPDATE SKIP LOCKED` is genuinely sufficient at this scale |
| D8 | Ports and adapters for every external service | Lets the entire pipeline run in tests with no network and no API keys |
| D9 | Single user, single-password gate | No multi-tenancy requirement exists; auth complexity would be pure overhead |
| D10 | No tracking pixels, no link shorteners | Deliverability. These are the dominant spam signals for personal-domain cold mail |

---

## 5. Architecture

Two processes and one database:

- **api** — FastAPI, serves the React dashboard's JSON API.
- **worker** — APScheduler; claims due work from Postgres and invokes use cases.
- **Postgres** — domain state *and* the work queue.

### Layering

```
backend/app/
  domain/        Pure Python. Entities, the outreach state machine,
                 candidate-pattern rules, targeting/ranking, the slop
                 validator. Imports no framework, no ORM, performs no IO.
  ports/         Protocols only: YCDirectory, FounderSource, EmailVerifier,
                 MailSender, MailReader, Drafter, ReplyClassifier,
                 ResumeParser, Clock, UnitOfWork.
  adapters/      Real implementations: YcOssClient, YcPageScraper,
                 ReoonVerifier, GmailSender, GmailReader, ClaudeDrafter,
                 ClaudeClassifier, SQLAlchemy repositories.
  application/   One use case per pipeline stage: SyncCompanies,
                 ScrapeFounders, ResolveEmails, ParseResume, DraftOutreach,
                 ApproveOutreach, SendApproved, PollReplies,
                 ScheduleFollowUps.
  api/           Thin FastAPI routers: parse -> call use case -> serialise.
  worker/        APScheduler job definitions and the claim loop.
```

**The rule that keeps this clean:** `domain/` and `application/` may not import
`adapters/`. Dependencies point inward. Use cases receive ports by constructor
injection, so every use case is fully testable against in-memory fakes.

---

## 6. Domain Model and State Machine

One `Outreach` row per founder. `sequence_step` is 0 for the initial email, 1
and 2 for the two follow-ups.

**States**

- `NEW` — founder discovered, nothing attempted.
- `NEEDS_EMAIL` — awaiting email resolution.
- `NEEDS_MANUAL_EMAIL` — resolution produced only catch-all or unknown verdicts;
  ranked candidates are shown in the dashboard for the user to pick or type.
- `UNRESOLVED` — no usable address, user chose to skip. Terminal.
- `READY_TO_DRAFT` — has a usable address, awaiting a draft for the current step.
- `PENDING_APPROVAL` — draft exists, waiting on the user.
- `APPROVED` — user approved; eligible for sending.
- `SENT` — message delivered to Gmail; `next_action_at` set to the follow-up due date.
- `REPLIED` — a human reply was received and classified.
- `CLOSED` — sequence finished with no human reply, or a terminal outcome reached.
- `DISCARDED` — user rejected this founder. Terminal.
- `BOUNCED` — hard bounce detected. Terminal.
- `FAILED` — exceeded retry budget; `last_error` is displayed. Terminal until reset.

**Transitions**

```
NEW -> NEEDS_EMAIL -> READY_TO_DRAFT | NEEDS_MANUAL_EMAIL
NEEDS_MANUAL_EMAIL -> READY_TO_DRAFT | UNRESOLVED
READY_TO_DRAFT -> PENDING_APPROVAL -> APPROVED | DISCARDED
APPROVED -> SENT
SENT --(no human reply, next_action_at reached, step < 2)--> READY_TO_DRAFT (step + 1)
SENT --(no human reply, step == 2, next_action_at reached)--> CLOSED
SENT --(human reply)--> REPLIED -> CLOSED
SENT --(hard bounce)--> BOUNCED
any non-terminal --(attempts > 5)--> FAILED
```

**Invariant:** a transition into `SENT` is only legal from `APPROVED`. Enforced
in the domain state machine and again by a database constraint (see §13).

---

## 7. Data Model

- **companies** — `id`, `yc_id`, `slug`, `name`, `website`, `domain`, `batch`,
  `team_size`, `regions` (jsonb), `all_locations`, `status`, `stage`,
  `is_hiring`, `one_liner`, `long_description`, `last_synced_at`.
- **job_postings** — `id`, `company_id`, `title`, `location`, `url`, `raw` (jsonb).
- **founders** — `id`, `company_id`, `yc_user_id`, `full_name`, `title`,
  `linkedin_url`, `twitter_url`, `bio`, `scraped_at`. Unique on `yc_user_id`.
- **email_candidates** — `id`, `founder_id`, `address`, `pattern`, `priority`,
  `verdict` (`valid` | `invalid` | `catch_all` | `unknown`), `provider`,
  `verified_at`. Unique on `(founder_id, address)`.
- **outreach** — `id`, `founder_id` (unique), `state`, `resolved_email`,
  `sequence_step`, `next_action_at`, `attempts`, `locked_at`, `last_error`,
  `created_at`, `updated_at`.
- **messages** — `id`, `outreach_id`, `sequence_step`, `direction`
  (`outbound` | `inbound`), `subject`, `body`, `gmail_message_id`,
  `gmail_thread_id`, `approved_at`, `sent_at`, `received_at`, `edited_by_user`.
- **outcomes** — `id`, `outreach_id`, `label`, `confidence`, `rationale`,
  `classified_at`, `source_message_id`.
- **resume_profile** — single row: `raw_text`, `claims` (jsonb array of
  structured, citable facts), `parsed_at`.
- **settings** — single row: `daily_send_cap`, `send_window_start`,
  `send_window_end`, `followup_day_1`, `followup_day_2`, `signature`.

There is no separate queue table. The worker selects due `outreach` rows by
`(state, next_action_at <= now())` with `FOR UPDATE SKIP LOCKED`.

---

## 8. Pipeline Stages

1. **SyncCompanies** (nightly). Fetch `all.json`; keep companies where
   `regions` or `all_locations` contains "India", `status == "Active"`, and
   `11 <= team_size <= 200`; upsert. Derive `domain` from `website` by stripping
   scheme, `www.` and path.
2. **ScrapeFounders**. For each company, GET its YC page, extract the
   `data-page` attribute, HTML-unescape, JSON-parse, read
   `props.company.founders[]` and `props.jobPostings[]`; upsert founders and job
   postings; create an `Outreach` row in `NEW` for each new founder. Rate limit
   1 request/second; cache by ETag; a parse failure marks that company for retry
   and never aborts the run.
3. **ResolveEmails**. See §9.
4. **ParseResume**. One-off, re-runnable. Parse the uploaded resume into
   `resume_profile.claims` — a list of structured facts (skill, project, metric,
   role, education), each with the verbatim source span. Only these claims may
   be cited by the drafter.
5. **DraftOutreach**. See §10.
6. **ApproveOutreach**. Dashboard action. The user may edit the subject and body
   before approving; edits set `messages.edited_by_user = true` and are retained
   as style references for later drafts (§10).
7. **SendApproved**. Worker sends via the Gmail API inside the configured send
   window, respecting `daily_send_cap`, with randomised inter-send delays.
   Persists `gmail_message_id` and `gmail_thread_id`. Sets `next_action_at` to
   `sent_at + followup_day_1`.
8. **PollReplies** (every 15 minutes). For each tracked `gmail_thread_id`, fetch
   the thread, persist any inbound messages not already stored, and classify
   (§11).
9. **ScheduleFollowUps**. Move `SENT` rows whose `next_action_at` has passed and
   that have no human reply to `READY_TO_DRAFT` with `sequence_step + 1`, or to
   `CLOSED` when `sequence_step == 2`.

---

## 9. Email Resolution Algorithm

**Input:** `full_name` (exact, from YC) and `domain` (from the company website).

**Normalisation:** lowercase, strip diacritics, remove non-alphabetic
characters. Names with three or more parts use the first token as `first` and
the last token as `last`. Mononyms produce only the `first` patterns.

**Candidate patterns, in priority order.** Ordering matters: it is what keeps a
full run inside the free verification tier. For `Pratish Gandhi` at `onefin.com`:

| Priority | Pattern | Example |
| --- | --- | --- |
| 1 | `{first}` | `pratish@onefin.com` |
| 2 | `{first}.{last}` | `pratish.gandhi@onefin.com` |
| 3 | `{f}{last}` | `pgandhi@onefin.com` |
| 4 | `{first}{last}` | `pratishgandhi@onefin.com` |
| 5 | `{first}_{last}` | `pratish_gandhi@onefin.com` |
| 6 | `{first}{l}` | `pratishg@onefin.com` |
| 7 | `{last}` | `gandhi@onefin.com` |

**Procedure.** Verify candidates one at a time in priority order, stopping at
the first `valid`. Persist every verdict. Expected cost is roughly 2.5
verifications per founder — about 480 for a full 192-founder run, inside Reoon's
600/month free tier.

**Outcomes.** First `valid` -> store as `resolved_email`, move to
`READY_TO_DRAFT`. All candidates `invalid` -> `NEEDS_MANUAL_EMAIL`. Domain is
`catch_all` (common on Google Workspace, which accepts every address at SMTP
time) -> do **not** guess; move to `NEEDS_MANUAL_EMAIL` and present the ranked
candidates plus the founder's LinkedIn URL so the user can decide.

**Quota exhaustion** pauses the stage, raises a dashboard banner, and resumes
the next day when the free tier renews. It is never treated as a verification
failure.

---

## 10. Drafting and Anti-Slop Guardrails

**Drafter inputs:** `resume_profile.claims`; the company's `name`, `one_liner`
and `long_description`; matching `job_postings`; the founder's `full_name`,
`title` and `bio`; the sequence step; the thread history for follow-ups; and up
to three previously user-edited messages as style references.

**Output:** plain text only — a subject and a body. No markdown, no HTML.

**The slop validator is deterministic domain code, not a prompt instruction.**
It runs on every draft and is unit-testable in isolation. A draft is rejected if:

- the body exceeds 150 words;
- it contains a banned phrase — "I hope this email finds you well",
  "I am reaching out", "passionate about", "leverage", "synergy",
  "game-changing", "I came across your company", "delve", "tapestry",
  "in today's fast-paced";
- it does not name the company at least once;
- it contains no company-specific token — a job title from `job_postings`, or a
  distinctive term from `one_liner` / `long_description`;
- it asserts a skill, metric or role absent from `resume_profile.claims`;
- it contains more than two em dashes;
- the subject is not between three and eight words, or contains emoji or all-caps words;
- for a follow-up (`sequence_step > 0`), its body is not shorter in words than
  the previous outbound message in the same thread.

**On failure:** retry once with the validation errors appended to the prompt. A
second failure moves the row to `PENDING_APPROVAL` flagged *needs manual write*,
with the rejected draft and the reasons shown. The pipeline never silently
sends a draft that failed validation, and never discards work without showing it.

---

## 11. Reply Classification

Labels: `INTERESTED`, `REJECTED`, `REFERRAL`, `AUTO_REPLY`, `OUT_OF_OFFICE`,
`UNCLEAR`.

Each classification stores `confidence` and a one-line `rationale` displayed in
the dashboard, so a wrong label is always visible and correctable by hand.

**Only human labels cancel a sequence** — `INTERESTED`, `REJECTED`, `REFERRAL`
and `UNCLEAR` move the row to `REPLIED`. `AUTO_REPLY` and `OUT_OF_OFFICE` are
recorded but leave `next_action_at` untouched, so an autoresponder cannot
silently end an outreach.

---

## 12. Follow-Up Policy

Follow-up 1 at +5 days, follow-up 2 at +12 days, both configurable in
`settings`. Each is drafted with full thread history, must be shorter than the
message before it, and goes through the same approval queue and the same slop
validator. Any human reply cancels all remaining steps. After step 2 with no
human reply, the row moves to `CLOSED`.

---

## 13. Error Handling and Idempotency

- **Transient failures** (network, HTTP 429, HTTP 5xx) retry with exponential
  backoff and jitter, incrementing `attempts`. At `attempts > 5` the row moves
  to `FAILED` with `last_error` surfaced in the dashboard.
- **Double-send prevention — reserve before sending.** A unique constraint on
  `(outreach_id, sequence_step)` over outbound `messages` is the guard, but the
  ordering matters and is not optional:

  1. **Reserve**: in its own committed transaction, insert the outbound
     `messages` row with `sent_at` still null. The unique constraint means a
     second worker, or a retry after a crash, fails here and stops.
  2. **Send**: call the Gmail API.
  3. **Record**: update the reserved row with `gmail_message_id`,
     `gmail_thread_id` and `sent_at`, and advance the outreach state.

  Sending before reserving would be a real double-send bug: if the Gmail call
  succeeds and the transaction then fails, the retry finds no row to conflict
  with and emails the founder a second time. Reserving first converts that
  failure mode into a recoverable one — a reserved row with `sent_at` null and a
  stale `locked_at` is surfaced in the dashboard as *needs verification* and is
  reconciled against Gmail by message id rather than blindly resent. This is the
  most important safety property in the system.
- **Approval cannot be bypassed.** A database `CHECK` constraint requires
  `approved_at IS NOT NULL` on any outbound message row with `sent_at` set, and
  the `SendApproved` use case asserts the same. A bug in either layer alone
  cannot send an unapproved email.
- **Worker claims** set `locked_at`; a stale lock older than 15 minutes is
  reclaimable, so a killed worker does not strand rows.
- **Gmail quota or auth errors** pause sending, raise a dashboard banner, and
  retry in the next window rather than burning retry budget.
- **Hard bounces** detected during reply polling move the row to `BOUNCED` and
  mark the address `invalid` so it is never retried.

---

## 14. Security and Secrets

Gmail OAuth client credentials, the Reoon API key and the Anthropic API key are
read from environment variables via a typed settings object, and are never
committed. `.env` is git-ignored; `.env.example` documents every required
variable with placeholder values. OAuth refresh tokens are stored in Postgres,
not in files. The dashboard sits behind a single-password gate; the resume and
all outreach content stay on the user's own infrastructure.

---

## 15. Testing Strategy

- **Domain** — pure unit tests with no mocks: state machine transitions
  (including every illegal transition), candidate generation and ordering, name
  normalisation edge cases (mononyms, three-part Indian names, diacritics),
  targeting filters, and the slop validator rule by rule.
- **Application** — each use case against in-memory fakes for every port. The
  complete pipeline runs end to end in tests with no network.
- **Adapters** — one shared contract test suite executed against both the fake
  and the real implementation of each port, so the fakes cannot drift. Real
  adapters are marked `@pytest.mark.integration` and skip cleanly without
  credentials. YC and Reoon HTTP interactions use recorded fixtures.
- **API** — FastAPI `TestClient` wired to fakes.
- **Frontend** — Vitest and React Testing Library, MSW for API mocking, with the
  approval queue covered most heavily as the highest-risk surface.

**Definition of done for the backend: `pytest` passes with no environment
variables set and no network access.**

---

## 16. Frontend

React with Vite, JSX, TanStack Query for server state. Four screens:

- **Pipeline** — funnel counts by state, stage health, quota banners, failures.
- **Review Queue** — the primary workspace. Draft on the left, company and
  founder context on the right. Edit, approve, reject. Keyboard-driven, because
  the user will move through roughly 192 drafts.
- **Contacts** — all 96 companies and their founders, filterable by state,
  hiring status, batch and email-resolution status.
- **Thread** — per-outreach timeline: every outbound message, every reply, the
  classified outcome with its rationale.

---

## 17. Deployment and Operations

`docker compose up` starts Postgres, the API and the worker. The frontend runs
via `npm run dev` in development and builds to static assets served by the API
in production. Schema changes go through Alembic migrations. Structured JSON
logging throughout; every use case logs its stage, the outreach id and the
outcome.

---

## 18. Deliverability and Compliance

Roughly 192 cold emails sent from a personal Gmail account over a short window
can trip spam heuristics. Mitigations, all built in:

- default `daily_send_cap` of 20, spreading a full run over about ten days;
- randomised delays between sends inside the configured window;
- plain text only, with no tracking pixels and no link shortening;
- a real signature and a single-line opt-out sentence in every email;
- hard-bounce handling that immediately stops retrying a bad address.

This is individual job-seeking correspondence rather than bulk commercial
marketing, so it does not fall under bulk-marketing regimes. The opt-out line is
included regardless, as courtesy and because it improves reply quality.

---

## 19. Cost

| Item | Cost |
| --- | --- |
| YC company data (`yc-oss`) | Free |
| YC founder scraping | Free |
| Reoon Email Verifier | Free tier: 600 verifications/month for life. A full run needs ~480 |
| Gmail sending | Free — 500/day limit against a need of ~20/day |
| Claude drafting and classification | Roughly INR 150–250 for a full 192-founder run |
| Hosting | Local, or a free tier |
| **Total to run** | **INR 0** |

**Optional safety net:** Reoon's smallest one-time pack is 10,000 credits for
$11.90 (about INR 1,050), and credits never expire — roughly seven full re-runs.
Not required to start.

---

## 20. Success Criteria

1. A nightly sync produces 96 target companies without manual intervention.
2. Founder scraping yields roughly 192 founders with name, title and LinkedIn.
3. At least 70% of founders reach a `valid` verified address; the remainder land
   in `NEEDS_MANUAL_EMAIL` with ranked candidates, never in a silent failure.
4. 100% of outbound messages carry a non-null `approved_at`.
5. Zero duplicate sends, enforced by constraint and proven by a test that
   simulates a crash mid-send.
6. Every reply receives a label and a visible rationale; auto-replies never
   cancel a sequence.
7. `pytest` passes offline with no credentials.
8. The dashboard shows, for any founder, the full timeline and current outcome.

---

## 21. Future Work (Explicitly Deferred)

Widening beyond India or beyond the 11–200 band; other job sources such as
Lever, Greenhouse and Ashby; multi-user support; A/B testing of email variants;
LinkedIn outreach; and reply-draft assistance for the user's own responses.
