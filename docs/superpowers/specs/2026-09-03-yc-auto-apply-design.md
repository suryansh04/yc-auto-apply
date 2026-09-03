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
- Per-company research: news items, YC launch posts and job postings, used to
  say what the company does and to ground a specific, factual reason for
  reaching out.
- **Outreach to all 96 target companies regardless of current hiring status**
  (D19) — both the 34 currently showing open roles and the 62 that are not.
  `isHiring` / `jobPostings` are a personalisation source (D17) and a
  review-queue ordering signal, never a targeting filter.
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
| ...of which currently hiring (`isHiring` with `jobPostings` present) | 34 |
| ...not currently hiring | 62 |
| ...with a usable website | 96 |

**All 96 are targeted, both groups (D19).** The 34/62 split is not a filter —
it changes only which research source grounds the "why I like this company"
line (D17: a matching job posting for the 34, news / launch / long_description
for the other 62) and, in the dashboard, which drafts are queued for review
first. Every founder at all 96 companies is sent an email.

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

**The same payload also carries real per-company research material, at zero
extra cost.** The `data-page` fetch already used for founders (§8, stage 2)
returns three more arrays in one request:

| Field | Coverage (10 hiring companies sampled) | Content |
| --- | --- | --- |
| `props.newsItems[]` | 6/10 non-empty, avg 1.3 items | Dated, sourced press headlines — e.g. *"OpenAI backs Indian startup Vahan's tech to automate blue-collar hiring with voice AI" — The Economic Times, May 04, 2025* |
| `props.launches[]` | 1/10 non-empty | The founder's own published product post — problem, solution, in their own words. Vahan's launch post ends with a founder-published email address (`madhav@vahan.ai`) |
| `props.jobPostings[]` | 10/10 non-empty, avg 4.2 postings | Beyond `title`: `location`, `roleSpecificType`, `salaryRange`, `minExperience`, `skills[]`, `visa` |

`isHiring` is not fully reliable on its own — one sampled company (OneFin) has
`isHiring: true` with zero `jobPostings`, so the ranking and drafting logic
must check `jobPostings` directly rather than trusting the flag alone.

News items and launch posts are exactly the raw material a human would use to
write "why I like your company": specific, dated, sourced facts rather than
generic praise. This closes the gap identified in the founder-data table above
— founder *bio* text was thin (13/28), but company-level research material is
present for the majority of companies and costs nothing beyond a request
already being made.

**Consequence:** the only step that needs a paid third party is
`exact full name + company domain -> verified email address`, and even that has
a free bypass whenever a `launches[]` post happens to publish one.

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
| D7 | Modular monolith: FastAPI app + one worker process per pipeline stage + Postgres, DB-as-queue | Production-shaped without Redis or Celery; `SELECT ... FOR UPDATE SKIP LOCKED` is genuinely sufficient at this scale. Splitting into stage-scoped worker processes (D20) does not require either dependency — it is still N small Python entrypoints against the same queue |
| D8 | Ports and adapters for every external service | Lets the entire pipeline run in tests with no network and no API keys |
| D9 | Single user, single-password gate | No multi-tenancy requirement exists; auth complexity would be pure overhead |
| D10 | No tracking pixels, no link shorteners | Deliverability. These are the dominant spam signals for personal-domain cold mail |
| D11 | Gemini Flash for drafting and reply classification, not Sarvam | Evaluated 2026-09-03. Both cost a broadly comparable, trivial amount per full run (§19), so cost does not decide it. Sarvam's differentiator is 11 Indic languages and this workload is entirely English, so that capability goes unused while general English prose quality — the core requirement — suffers. Gemini also has first-class `responseSchema` structured output, where Sarvam's Python SDK requires passing `response_format` through `request_options={"additional_body_parameters": {...}}` |
| D12 | Free tier for development, paid tier for real runs | Gemini's free tier (1,500 requests/day, far above the ~70/day peak here) may use inputs and outputs for model training. Real runs send the user's resume and ~192 founders' names and addresses, so production runs use the paid tier, at roughly INR 60-65 per full run including the voice critic (D22, §19) |
| D13 | The model id is configuration, not code | `GEMINI_MODEL` env var pins the exact model. Flash generations turn over quickly and Google has published a price increase effective 2027-01-01 for some of them, so the adapter must never hardcode a model id |
| D14 | Pay for the paid tier by UPI through Google Cloud Billing | Verified 2026-09-03. The paid tier requires a linked Cloud Billing account, and Cloud Billing in India accepts UPI given an Indian billing address and bank account, INR billing, a registered UPI account and a one-time prepayment of about INR 500-1000. No international card is needed. At ~INR 60-65 per run (§19) an INR 500 prepayment covers roughly eight full runs. Google's wording is "if you're a qualified organization in India", so individual-account eligibility should be confirmed in the console before relying on it |
| D15 | No third-party INR/UPI LLM reseller proxies | Resellers do solve INR and UPI billing, but every prompt would route through an unvetted intermediary, and prompts here carry the user's resume plus ~192 founders' names and email addresses. That is worse for third-party personal data than the free tier's published policy. If UPI cannot be attached, the fallback is Sarvam (D11 notes the quality trade-off) or staying on the free tier for development only |
| D16 | Company research reuses the founder-scrape request; no new API, no separate fetch | `newsItems[]`, `launches[]` and `jobPostings[]` already arrive in the `data-page` payload fetched in stage 2 (§8). Adding a second research call per company would double the request volume against YC for data already in hand |
| D17 | Only `newsItems`, `launches` and verified resume claims may be cited as reasons for outreach; the drafter may not invent a "why I like this company" from the `one_liner`/`long_description` alone | A generic description restated as enthusiasm is the clearest tell of automated outreach. Dated, sourced facts (a funding round, a founder's own launch post) are checkable and specific; a one-liner is marketing copy every recipient has already read about their own company |
| D18 | A founder-published email found in a `launches[]` post outranks every generated pattern | It is the founder's own address, in their own words, requiring no verification. Checked first in §9, before pattern generation |
| D19 | `isHiring` / open `jobPostings` is a review-queue ordering and personalisation signal only — never a targeting filter. All 96 companies (34 currently hiring, 62 not) get an `Outreach` row for every founder | Explicit user requirement 2026-09-03: outreach must cover companies without a current opening as well as those with one. The pipeline never gated on `isHiring` (§8 never referenced it as a filter), but this makes the guarantee explicit and traceable rather than implicit, and closes off a plausible future misreading of "rank hiring-first" as "hiring-only" |
| D20 | Each background pipeline stage runs as its own worker process, not one process running every stage | Explicit user requirement 2026-09-03: "different agents, different responsibility, not all at once." Confirmed as process decomposition, not LLM-agent autonomy — the LLM is still called as a pure structured-output function at exactly two points (draft, classify), unchanged from D8/D11. Each stage worker is independently deployable, restartable and observable: a stuck or crashed `SendWorker` no longer blocks `DraftWorker` from continuing to produce drafts for review, which was a real weakness of the single-process design. No new infrastructure — still Postgres-as-queue (D7), just N small entrypoints against it instead of one |
| D21 | No APScheduler; each stage worker owns one tiny interval-or-cron loop over its single job | With one job per process, a general-purpose multi-job scheduler is unused generality. A ~20-line `run_forever(fn, every=...)` / `run_daily(fn, at=...)` helper in `worker/scheduling.py`, shared by all seven entrypoints, replaces it — one dependency removed, same behaviour |
| D22 | Add a second LLM pass — a voice critic — after the deterministic validator, advisory only, never a gate | Explicit user requirement 2026-09-03. A fixed rule list cannot catch a draft that is technically clean but still reads generic or template-like; a language-judging pass can. Scoped narrowly to avoid reopening the fabrication risk of a fully autonomous research agent: the critic judges only tone on an already fact-checked draft, cannot introduce new claims, cannot block or approve a send by itself, and every verdict — `PASS` included — still goes to the human approval queue (D5), which stays the only step that can actually authorise a send |

---

## 5. Architecture

One API process, seven single-responsibility stage-worker processes, and one
database (D20). Each worker does exactly one job, in its own loop, and none of
them talk to each other directly — they only ever claim and update rows in
Postgres:

| Process | Pipeline stage (§8) | Loop |
| --- | --- | --- |
| **api** | Serves the React dashboard's JSON API; also runs `ParseResume` and `ApproveOutreach` synchronously on user action (§8 note) | Request/response, not a loop |
| **sync-worker** | 1. SyncCompanies | Daily, at a fixed time (`run_daily`) |
| **scrape-worker** | 2. ScrapeCompanyPage | Every N hours (`run_forever`) |
| **resolve-worker** | 3. ResolveEmails | Every N minutes, pauses on Reoon quota exhaustion (§9) |
| **draft-worker** | 5. DraftOutreach | Every N minutes; the only worker besides reply-poll-worker that calls Gemini |
| **send-worker** | 7. SendApproved | Every N minutes inside `send_window`; owns `daily_send_cap` accounting |
| **reply-poll-worker** | 8. PollReplies | Every 15 minutes; the other Gemini caller (classification) |
| **followup-worker** | 9. ScheduleFollowUps | Every N minutes |
| **Postgres** | — | domain state *and* the work queue for all seven workers |

Stages 4 (`ParseResume`) and 6 (`ApproveOutreach`) are **not** background
workers — they are ordinary use cases the `api` process calls synchronously
when the user uploads a resume or clicks approve/reject. Giving them a
standing worker process would mean polling for events (an upload, a click)
that the API already knows about the instant they happen.

Each stage worker crashing or being restarted affects only its own stage: a
`send-worker` outage stops sends, but `draft-worker` keeps producing drafts for
review in the meantime, and `sync-worker`/`scrape-worker` keep the company data
current. This was a real gap in the single-process design — one stuck job used
to be able to starve every other stage of scheduler time in the same process.

### Layering

```
backend/app/
  domain/        Pure Python. Entities, the outreach state machine,
                 candidate-pattern rules, the sync targeting filter (§8 stage
                 1), the review-queue rank_key (§8 stage 5, ordering only,
                 D19), the slop validator. Imports no framework, no ORM,
                 performs no IO.
  ports/         Protocols only: YCDirectory, FounderSource, EmailVerifier,
                 MailSender, MailReader, Drafter, DraftCritic (D22),
                 ReplyClassifier, ResumeParser, Clock, UnitOfWork.
  adapters/      Real implementations: YcOssClient, YcPageScraper,
                 ReoonVerifier, GmailSender, GmailReader, GeminiDrafter,
                 GeminiCritic (D22), GeminiClassifier, SQLAlchemy repositories.
  application/   One use case per pipeline stage: SyncCompanies,
                 ScrapeCompanyPage, ResolveEmails, ParseResume, DraftOutreach,
                 ApproveOutreach, SendApproved, PollReplies,
                 ScheduleFollowUps.
  api/           Thin FastAPI routers: parse -> call use case -> serialise.
  worker/        scheduling.py (run_forever / run_daily, D21) and the shared
                 claim-loop helper (SELECT ... FOR UPDATE SKIP LOCKED, §7),
                 used by seven tiny entrypoints — sync_worker.py,
                 scrape_worker.py, resolve_worker.py, draft_worker.py,
                 send_worker.py, reply_poll_worker.py, followup_worker.py.
                 Each entrypoint is a few lines: construct its adapters, call
                 run_forever/run_daily on its one use case. No stage-specific
                 logic lives here — that is all in application/.
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
- **job_postings** — `id`, `company_id`, `title`, `location`, `url`,
  `role_specific_type`, `salary_range`, `min_experience`, `skills` (jsonb),
  `raw` (jsonb).
- **founders** — `id`, `company_id`, `yc_user_id`, `full_name`, `title`,
  `linkedin_url`, `twitter_url`, `bio`, `scraped_at`. Unique on `yc_user_id`.
- **company_news** — `id`, `company_id`, `title`, `url`, `source`, `published_at`,
  `raw` (jsonb). Unique on `(company_id, url)`. From `newsItems[]`.
- **company_launches** — `id`, `company_id`, `title`, `body`, `published_email`,
  `raw` (jsonb). From `launches[]`. `published_email` is parsed out of `body`
  via a `mailto:`-style regex and feeds the email resolution priority list
  (§9, D18).
- **email_candidates** — `id`, `founder_id`, `address`, `pattern`, `priority`,
  `verdict` (`valid` | `invalid` | `catch_all` | `unknown` | `published`),
  `provider`, `verified_at`. Unique on `(founder_id, address)`.
- **outreach** — `id`, `founder_id` (unique), `state`, `resolved_email`,
  `sequence_step`, `next_action_at`, `attempts`, `locked_at`, `last_error`,
  `created_at`, `updated_at`.
- **messages** — `id`, `outreach_id`, `sequence_step`, `direction`
  (`outbound` | `inbound`), `subject`, `body`, `research_source_type`
  (`news` | `launch` | `job_posting` | `long_description`, nullable — inbound
  messages have none), `research_source_id`, `critic_verdict` (`pass` |
  `needs_revision`, nullable — inbound messages have none), `critic_concerns`
  (jsonb array of `{span, issue}`, D22), `critic_overall_note` (maps 1:1 from
  the critic's `overall_note` field below), `gmail_message_id`,
  `gmail_thread_id`, `approved_at`, `sent_at`, `received_at`, `edited_by_user`.
- **outcomes** — `id`, `outreach_id`, `label`, `confidence`, `rationale`,
  `classified_at`, `source_message_id`.
- **resume_profile** — single row: `raw_text`, `claims` (jsonb array of
  structured, citable facts), `parsed_at`.
- **settings** — single row: `daily_send_cap`, `send_window_start`,
  `send_window_end`, `followup_day_1`, `followup_day_2`, `signature`.

There is no separate queue table. Each stage worker (D20) selects the
`outreach` rows due for *its own* stage — the state(s) it owns plus
`next_action_at <= now()` — with `FOR UPDATE SKIP LOCKED`. A row is only ever
in a state one worker cares about at a time, so the seven workers never
contend over the same rows, only over the same table.

---

## 8. Pipeline Stages

1. **SyncCompanies** (nightly). Fetch `all.json`; keep companies where
   `regions` or `all_locations` contains "India", `status == "Active"`, and
   `11 <= team_size <= 200`; upsert. Derive `domain` from `website` by stripping
   scheme, `www.` and path.
2. **ScrapeCompanyPage** (formerly "ScrapeFounders" — one fetch now populates
   founders and all research material, D16). For each company, GET its YC page,
   extract the `data-page` attribute, HTML-unescape, JSON-parse, and read four
   arrays from the one payload: `props.company.founders[]`,
   `props.jobPostings[]`, `props.newsItems[]` and `props.launches[]`. Upsert
   founders, job postings, `company_news` and `company_launches`; parse any
   `mailto:`-style address out of each launch body into
   `company_launches.published_email`; create an `Outreach` row in `NEW` for
   each new founder. Rate limit 1 request/second; cache by ETag; a parse
   failure marks that company for retry and never aborts the run. Do not trust
   `isHiring` alone when ranking or drafting — one sampled company carried
   `isHiring: true` with zero `jobPostings`; always check `jobPostings` directly.
3. **ResolveEmails**. See §9.
4. **ParseResume**. One-off, re-runnable. Parse the uploaded resume into
   `resume_profile.claims` — a list of structured facts (skill, project, metric,
   role, education), each with the verbatim source span. Only these claims may
   be cited by the drafter.
5. **DraftOutreach**. See §10. Processes every `READY_TO_DRAFT` row — from all
   96 companies, hiring or not (D19) — with no filtering by `isHiring`. The one
   place hiring status affects anything is `rank_key`, a computed (not stored)
   domain sort used only for **review-queue display order**: rows for
   companies with at least one open `job_posting` sort before rows without one,
   tie-broken by `team_size` descending, then `founder.full_name`. It changes
   which drafts the user reviews first; it never changes who gets drafted or
   sent.
6. **ApproveOutreach**. Dashboard action. The user may edit the subject and body
   before approving; edits set `messages.edited_by_user = true` and are retained
   as style references for later drafts (§10).
7. **SendApproved**. `send-worker` (D20) sends via the Gmail API inside the
   configured send window, respecting `daily_send_cap`, with randomised
   inter-send delays.
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

**Step 0 — published email first (D18).** Before generating any pattern, check
`company_launches.published_email` for the founder's company. A launch post is
authored by one person, so only attribute the address automatically when
either (a) the company has exactly one founder, or (b) the founder's
`full_name` (or first name) appears in the launch `body` near the address. When
attributable, store it as an `email_candidates` row with `verdict='published'`,
skip pattern generation and verification entirely for that founder, and move
straight to `READY_TO_DRAFT` — it is already a real, working address with no
API cost. When a `published_email` exists but cannot be confidently attributed
to a specific founder, do not guess: surface it as a hint on the
`NEEDS_MANUAL_EMAIL` review row (§9 Outcomes) instead of auto-assigning it.

**Normalisation (for founders without a published email):** lowercase, strip
diacritics, remove non-alphabetic characters. Names with three or more parts
use the first token as `first` and the last token as `last`. Mononyms produce
only the `first` patterns.

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

**Outcomes.** A `published` verdict from Step 0 -> `READY_TO_DRAFT` immediately,
no verification call. Otherwise, first `valid` -> store as `resolved_email`,
move to `READY_TO_DRAFT`. All candidates `invalid` -> `NEEDS_MANUAL_EMAIL`.
Domain is `catch_all` (common on Google Workspace, which accepts every address
at SMTP time) -> do **not** guess; move to `NEEDS_MANUAL_EMAIL` and present the
ranked candidates, any unattributed `published_email` hint, and the founder's
LinkedIn URL so the user can decide.

**Quota exhaustion** pauses the stage, raises a dashboard banner, and resumes
the next day when the free tier renews. It is never treated as a verification
failure.

---

## 10. Drafting and Anti-Slop Guardrails

**Drafter inputs:** `resume_profile.claims`; the company's `name`, `one_liner`
and `long_description`; matching `job_postings`; the company's `company_news`
and `company_launches` (§7, populated free in stage 2, D16); the founder's
`full_name`, `title` and `bio`; the sequence step; the thread history for
follow-ups; and up to three previously user-edited messages as style
references.

**Required structure of a draft.** The user asked for the email to say what the
company does and why the sender specifically likes it, in a human voice — not
generic praise. The drafter is prompted to produce, and the domain model
requires, three grounded parts:

1. **What the company does** — one sentence, in the sender's own words, not a
   copy of `one_liner`. Paraphrase is required by the validator (see below).
2. **A specific, cited reason for reaching out to *this* company** — must be
   grounded in one real source, in priority order: a `company_news` item, a
   `company_launches` post, a matching `job_posting`, or (only when none of the
   first three exist) a concrete mechanism or detail from `long_description`
   rather than the marketing `one_liner`. Restating `one_liner` back at the
   founder is exactly the "I came across your company" pattern this system
   exists to avoid (D17).
3. **Why the sender fits** — must cite only `resume_profile.claims`; never an
   invented skill or metric.

**Structured output, not free text (D11).** The drafter calls Gemini with a
`responseSchema` returning `{subject, body, research_source_type,
research_source_id}`, where `research_source_type` is one of `news` |
`launch` | `job_posting` | `long_description` and `research_source_id` names
the specific `company_news`, `company_launches` or `job_postings` row cited in
part 2. This is what lets the validator check grounding mechanically instead of
trusting the prose, and lets the approval queue (§16) show the reviewer exactly
which fact the draft is standing on.

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
- **the "what they do" sentence has more than a 60% word-overlap with
  `one_liner`** — a paraphrase check, so part 1 cannot just quote the tagline;
- **`research_source_type` / `research_source_id` do not resolve to a real row**
  for this company, or the cited row's own text shares no distinctive term
  (a proper noun, a number, a named person) with the "why" sentence — this is
  the grounding check for part 2, and it is what stops the model from
  fabricating a plausible-sounding but untrue reason;
- it asserts a skill, metric or role absent from `resume_profile.claims`;
- it contains more than two em dashes;
- the subject is not between three and eight words, or contains emoji or all-caps words;
- for a follow-up (`sequence_step > 0`), its body is not shorter in words than
  the previous outbound message in the same thread.

**When no research material exists for a company** (no `company_news`, no
`company_launches`, and a `long_description` under 80 characters — 7 of the 96
target companies measured this way), the `long_description` fallback for part 2
also fails, and the row moves straight to `PENDING_APPROVAL` flagged *needs
manual write* rather than letting the model strain a generic sentence into
looking specific. This is the honest outcome: roughly 7 of 96 companies do not
currently have enough public material for a genuinely specific opener, and a
human writes those by hand.

**On failure:** retry once with the validation errors appended to the prompt. A
second failure moves the row to `PENDING_APPROVAL` flagged *needs manual write*,
with the rejected draft and the reasons shown. The pipeline never silently
sends a draft that failed validation, and never discards work without showing it.

**The voice critic (D22) — a second, judgment-based pass, advisory only.**
The deterministic validator above catches hard, checkable violations (banned
phrases, grounding, length). It cannot catch a draft that is technically clean
but still *reads* generic, stiff or template-like — the kind of thing a person
notices in one glance but no fixed rule list captures. For that, once a draft
passes the deterministic validator, `draft-worker` (§5 — this is a step inside
the existing `DraftOutreach` use case, not a new pipeline stage or worker) makes
one more Gemini call with a `responseSchema` returning:

```
{
  verdict: "PASS" | "NEEDS_REVISION",
  concerns: [ { span: string, issue: string } ],   // 0-3 items
  overall_note: string                              // <= 20 words
}
```

`span` quotes the exact phrase the critic is flagging; `issue` is a short,
specific reason ("this opener could be sent to any company", "no person
actually writes 'the innovative work you're doing'"). The critic is asked to
judge only naturalness and specificity of tone, never facts — it is not
re-checking grounding, that is the deterministic validator's job and already
done.

**On `NEEDS_REVISION`:** exactly one automatic re-draft, with the critic's
`concerns` appended to the prompt — the same one-retry pattern as the
deterministic validator above, not a second, independent retry budget. The
re-drafted output goes back through the deterministic validator first (a fix
for tone could accidentally introduce a banned phrase or break grounding), then
through the critic once more. Whatever the state after that — `PASS`,
still `NEEDS_REVISION`, or the retry itself failed deterministic validation and
fell back to *needs manual write* — the draft moves to `PENDING_APPROVAL`
regardless, with the critic's latest verdict, `concerns` and `overall_note`
stored on the `messages` row (§7) and shown next to the draft in the Review
Queue (§16). **The critic can never block a send by itself and can never
approve one either** — it only ever adds information for the human review
step (D5), which stays the sole gate. Treat a `PASS` as one input, not a
reason to skim faster; the critic is another LLM, not a person, and can share
blind spots with the drafter that only a human reliably catches.

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
  reclaimable, so a killed worker does not strand rows. This now matters more
  than it did in the single-process design (D20): any one of the seven stage
  workers can be killed or redeployed independently, at any time, without
  affecting the other six.
- **Gmail quota or auth errors** pause sending, raise a dashboard banner, and
  retry in the next window rather than burning retry budget.
- **Hard bounces** detected during reply polling move the row to `BOUNCED` and
  mark the address `invalid` so it is never retried.

---

## 14. Security and Secrets

Gmail OAuth client credentials, the Reoon API key and the Google Gemini API key
are read from environment variables via a typed settings object, and are never
committed. `GEMINI_MODEL` pins the model id and `GEMINI_TIER` records whether
the configured key is free or paid, so a run against real founder data on a
free-tier key fails loudly rather than silently sending that data into a
training-eligible pipeline (D12). The paid key is obtained by linking a Google
Cloud Billing account funded by UPI (D14); on the paid tier Google states that
prompts and responses are "not used to improve Google products", which is what
makes real runs against founder data acceptable. `.env` is git-ignored; `.env.example` documents every required
variable with placeholder values. OAuth refresh tokens are stored in Postgres,
not in files. The dashboard sits behind a single-password gate; the resume and
all outreach content stay on the user's own infrastructure.

---

## 15. Testing Strategy

- **Domain** — pure unit tests with no mocks: state machine transitions
  (including every illegal transition), candidate generation and ordering, name
  normalisation edge cases (mononyms, three-part Indian names, diacritics),
  targeting filters, and the slop validator rule by rule — including the
  grounding check (§10): a draft citing a `research_source_id` that does not
  exist for the company must fail, a draft whose "why" sentence shares no
  distinctive term with its cited source must fail, and a "what they do"
  sentence that is a near-verbatim copy of `one_liner` must fail the paraphrase
  check. Cover the no-research-material fallback path explicitly (a company
  with no news, no launches and a short `long_description` must route to
  manual write, never to a strained generic draft). `rank_key` ordering:
  assert it only reorders, never filters — a fixture mixing hiring and
  non-hiring companies must produce `Outreach` rows and eligible drafts for
  every founder in both groups (D19), and a test on `DraftOutreach` must fail
  if any code path branches on `isHiring` to skip a row.
- **Application** — each use case against in-memory fakes for every port. The
  complete pipeline runs end to end in tests with no network. `DraftOutreach`
  specifically: a fake `DraftCritic` returning `PASS` moves straight to
  `PENDING_APPROVAL`; one returning `NEEDS_REVISION` must trigger exactly one
  re-draft call and no more, and the row must reach `PENDING_APPROVAL`
  regardless of the second verdict — never blocked, never auto-sent, and never
  looped past one retry (D22).
- **Adapters** — one shared contract test suite executed against both the fake
  and the real implementation of each port, so the fakes cannot drift. Real
  adapters are marked `@pytest.mark.integration` and skip cleanly without
  credentials. YC and Reoon HTTP interactions use recorded fixtures.
- **API** — FastAPI `TestClient` wired to fakes.
- **Worker entrypoints (D20)** — deliberately thin, so they carry no dedicated
  test suite of their own: each is a few lines wiring adapters and calling
  `run_forever`/`run_daily` (D21) on a use case already fully covered at the
  application layer. One smoke test per entrypoint asserts it wires the
  correct use case and port set, not stage behaviour — stage behaviour is the
  application layer's job. `scheduling.py` (`run_forever`, `run_daily`) gets
  its own unit tests: interval timing, daily-time triggering, and that an
  exception from one call does not kill the loop.
- **Frontend** — Vitest and React Testing Library, MSW for API mocking, with the
  approval queue covered most heavily as the highest-risk surface.

**Definition of done for the backend: `pytest` passes with no environment
variables set and no network access.**

---

## 16. Frontend

React with Vite, JSX, TanStack Query for server state. Four screens:

- **Pipeline** — funnel counts by state, stage health, quota banners, failures.
- **Review Queue** — the primary workspace. Ordered by `rank_key` (§8 stage 5)
  — display order only, every company's drafts appear, hiring or not (D19).
  Draft on the left, company and founder context on the right, including the
  specific `company_news` / `company_launches` / `job_posting` row named by
  `research_source_type` and `research_source_id` (§10) so the reviewer can
  check the cited fact at a glance rather than re-researching the company
  themselves. Also shown: the voice critic's `critic_verdict` and
  `critic_overall_note` (D22) as one small, dismissible line — a second opinion, not a
  gate, and not a reason to review any faster. Edit, approve, reject.
  Keyboard-driven, because the user will move through roughly 192 drafts.
- **Contacts** — all 96 companies and their founders — both hiring and not
  (D19) — filterable by state, hiring status, batch and email-resolution
  status. The hiring filter is a view control here, not a pipeline gate.
- **Thread** — per-outreach timeline: every outbound message, every reply, the
  classified outcome with its rationale.

---

## 17. Deployment and Operations

`docker compose up` starts Postgres, the API and all seven stage workers (D20)
as separate services — one command, nine containers, no per-worker manual
steps. Each worker's `docker-compose.yml` entry restarts independently
(`restart: unless-stopped`), so a crash in one does not affect the others or
require restarting the stack. The frontend runs via `npm run dev` in
development and builds to static assets served by the API in production.
Schema changes go through Alembic migrations. Structured JSON logging
throughout; every use case logs its stage, the outreach id and the outcome, and
every log line is tagged with the worker process name so the seven streams are
distinguishable in `docker compose logs`.

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
| Gemini Flash drafting, classification and voice critique (D22) | INR 0 on the free tier during development; about INR 60-65 per full run on the paid tier, which production runs use (D12) |
| Hosting | Local, or a free tier |
| **Total** | **INR 0 to develop; about INR 60-65 per real 192-founder run** |

A full run is roughly 450 drafts (192 founders, up to three messages each, plus
validator retries) and about 200 reply classifications — around 1.1M input and
155k output tokens, including the per-company research context (news items,
launch posts, job posting detail) added for grounding (§10); that context is a
few hundred tokens per draft against an already input-heavy prompt, so it does
not move the total meaningfully. At Gemini Flash rates of $0.25 / $1.50 per
million tokens that is $0.51. The voice critic (D22) adds one shorter call per
draft, plus a second call on whichever fraction comes back `NEEDS_REVISION`
and gets the one automatic retry — call it ~500 critic calls total at roughly
600 input / 150 output tokens each, another $0.19, for a full-run total near
$0.70 (~INR 62). Sarvam 105B, at INR 29.28 / 73.2 per million, comes to a
comparable figure for the same work, so the two remain equivalent on cost and
D11 was decided on prose quality and structured-output ergonomics instead.

**Optional safety net:** Reoon's smallest one-time pack is 10,000 credits for
$11.90 (about INR 1,050), and credits never expire — roughly seven full re-runs.
Not required to start.

---

## 20. Success Criteria

1. A nightly sync produces 96 target companies without manual intervention.
2. Founder scraping yields roughly 192 founders with name, title and LinkedIn,
   across all 96 companies — both the 34 currently hiring and the 62 that are
   not (D19). No founder is excluded on the basis of their company's hiring
   status.
3. At least 70% of founders reach a `valid` or `published` resolved address; the
   remainder land in `NEEDS_MANUAL_EMAIL` with ranked candidates, never in a
   silent failure.
4. 100% of outbound messages carry a non-null `approved_at`.
5. Zero duplicate sends, enforced by constraint and proven by a test that
   simulates a crash mid-send.
6. Every reply receives a label and a visible rationale; auto-replies never
   cancel a sequence.
7. `pytest` passes offline with no credentials.
8. The dashboard shows, for any founder, the full timeline and current outcome.
9. Of the ~89 target companies with usable research material (96 minus the ~7
   with no `company_news`, no `company_launches` and a `long_description` under
   80 characters, §10), at least 80 produce a draft whose "why this company"
   sentence is grounded in and traceable to one specific, named source row,
   verifiable by the reviewer in the Review Queue in under 10 seconds per
   draft. The remaining ~16 of 96 (7 with no material, plus grounding
   failures) route to manual write rather than a strained generic draft.
10. Stage-worker independence (D20) is demonstrable, not just claimed: with
    `send-worker` stopped, `draft-worker` continues producing new drafts into
    the Review Queue; restarting `send-worker` resumes sending with no data
    loss and no duplicate sends (§13). Each of the seven workers can be
    started, stopped and restarted independently via `docker compose
    stop/start <service>` without a full-stack restart.
11. Every draft reaching the Review Queue carries a `critic_verdict` (D22) —
    never null for an outbound message that passed the deterministic
    validator — and a `NEEDS_REVISION` verdict is demonstrably advisory: a
    test approving and sending a draft the critic flagged must succeed
    identically to approving a `PASS` draft. The critic can inform the human
    reviewer; it can never block or bypass them.

---

## 21. Future Work (Explicitly Deferred)

Widening beyond India or beyond the 11–200 band; other job sources such as
Lever, Greenhouse and Ashby; multi-user support; A/B testing of email variants;
LinkedIn outreach; and reply-draft assistance for the user's own responses.

**A Sarvam adapter is a deliberately cheap swap.** Because `Drafter` and
`ReplyClassifier` are ports with a shared contract test suite (§15), moving to
Sarvam — for Indian data residency, or to compare output quality — means adding
`SarvamDrafter` and `SarvamClassifier`, passing the same contract tests, and
changing one config value. No domain or application code changes. If both
providers are ever kept, the model attribution belongs on the `messages` row so
drafts stay traceable to the model that wrote them.
