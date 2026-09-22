# YC Auto Apply — Phase 1 + Phase 2 operator guide

The pipeline: sync the target YC companies, scrape their founders and
research material, resolve founder email addresses, draft one grounded
internship enquiry per founder, review every draft by hand, send the
approved ones through Gmail. Phase 1 (§1–10 below) built this as a
command-line tool. Phase 2 (§11) adds a browser UI over the same use
cases, reply polling, and a two-step follow-up sequence — no new pipeline
concept, no second way to do anything the CLI already does.

Everything in this file runs from this `backend/` directory against a
Postgres database, except the frontend (`../frontend`) and the scheduler
script (`../scripts/schedule-tasks.ps1`), covered in §11 and the root
`../README.md`.

Design specs: `../docs/superpowers/specs/2026-09-03-yc-auto-apply-design.md`
(Phase 1, §1–10 below) and
`../docs/superpowers/specs/2026-09-13-yc-auto-apply-phase-2-design.md`
(Phase 2, §11 below). Section numbers in §1–10 refer to the Phase 1 spec;
section numbers in §11 refer to the Phase 2 spec.

## 1. Prerequisites

- Python 3.11
- Postgres 16, one of:
  - **native**, listening on `5432` (the default `DATABASE_URL` below), or
  - **Docker**: `docker compose up -d` from `backend/` starts a container
    published on host port **5433** (`docker-compose.yml` maps `5433:5432`
    so it never collides with a native Postgres on 5432). Use
    `postgresql+psycopg://yc:yc@localhost:5433/ycautoapply` as
    `DATABASE_URL` in that case.
- Accounts: a MyEmailVerifier API key (free tier: 100 verifications/day),
  a Gemini API key (paid tier for the real run — see §3), a Gmail account
  and a Google Cloud OAuth client (Desktop app) for it.

```bash
cd backend
python -m venv .venv
```

Activate it, then install — pick the line for your shell (do not chain
these with `&&`; it is a parse error in Windows PowerShell 5.1):

```powershell
.venv\Scripts\Activate.ps1      # PowerShell
```

```bash
source .venv/Scripts/activate   # Git Bash
```

```bash
pip install -e ".[dev]"
```

`pip install -e` is required: without it `pytest` cannot import `app.*`.

## 2. Run everything from `backend/`

`.env` is read relative to the current working directory
(`app/config.py`: `env_file=".env"`). Every command in this guide — `alembic`,
`pytest`, `python -m app.cli ...` — assumes `backend/` is the CWD.

## 3. Configuration (`.env`)

Copy `.env.example` to `.env` and fill it in. Every variable maps to a field
of `app.config.Settings`; `.env.example` lists all ten.

| Variable | Meaning | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL for Postgres (`postgresql+psycopg://user:pass@host:port/db`). Port 5432 native, 5433 for the Docker container. **Required.** | — |
| `EMAIL_VERIFIER_API_KEY` | MyEmailVerifier API key (D30). The key is embedded in the request URL by the provider; the adapter redacts it from logs and never lets it into an exception. | empty |
| `GEMINI_API_KEY` | Google Gemini API key used by `parse-resume` and `draft`. | empty |
| `GEMINI_MODEL` | Model id passed to the Gemini SDK (D13 — never hardcoded). | `gemini-3.8-flash` |
| `GEMINI_TIER` | `free` or `paid`. On `free`, Google may train on inputs/outputs (D12): `parse-resume` warns (your own resume) and `draft` **refuses** unless `--allow-free-tier` is passed, because it sends founders' names, titles and bios. Set `paid` for the real run (D14: Cloud Billing accepts UPI in India; ~INR 95 per full run). | `free` |
| `LLM_PROVIDER` | `gemini` (default) or `sarvam`. Gemini's free tier allows only 20 requests/day per model; Sarvam (`sarvam-105b-conversations`, ~₹29/₹73 per 1M input/output tokens, UPI top-up at dashboard.sarvam.ai) costs on the order of ₹50 for a full run. | `gemini` |
| `SARVAM_API_KEY` | Sarvam subscription key (`sk_...`) when `LLM_PROVIDER=sarvam`. | empty |
| `SARVAM_MODEL` | Sarvam chat model id. `sarvam-105b` is a reasoning model that spends thousands of output tokens thinking; the `-conversations` variant does not and is ~40x cheaper. | `sarvam-105b-conversations` |
| `DAILY_SEND_CAP` | Maximum outbound emails per rolling 24-hour window (ruling R-A), counted against `messages.sent_at`. Gmail's own limit is 500/day; keep this low for deliverability (§18). | `20` |
| `SIGNATURE` | Your sign-off, appended at send time under the body (ruling R2). Multi-line: quote it in `.env`. `send` refuses a blank signature. | empty |
| `RESUME_PATH` | Path to the PDF attached to every email. `send` refuses if the file does not exist. | empty |
| `GMAIL_SENDER_ADDRESS` | The Gmail account `gmail-auth` authorised; also the recipient of `send --to-self`. | empty |
| `GMAIL_CLIENT_SECRETS_PATH` | Path to the OAuth client JSON downloaded from Google Cloud (APIs & Services → Credentials → OAuth client ID → *Desktop app*; enable the Gmail API; scope used is `gmail.send` only). | empty |

The four send-side settings default to empty on purpose: a fresh `.env`
cannot send anything by accident.

## 4. Database schema

```bash
alembic upgrade head
```

Must precede everything, including `pytest -m integration`. Migrations
`0001_initial` and `0002_gmail_credentials` create every table (§7),
including the CHECK constraints and the partial unique index that back the
reserve-before-send guard (§13, ruling R1).

## 5. Tests

```bash
pytest                      # offline: no env vars, no network, no DB
pytest -m integration       # 13 DB tests; skipped unless DATABASE_URL is in the environment (not just .env)
```

The offline suite runs against in-memory fakes and covers the domain rules,
every use case, the CLI wiring and an end-to-end run of the whole chain.
The integration suite exercises the real repositories, constraints and the
job runner's own-session commits against your Postgres (run `alembic
upgrade head` first). The test suite ignores `backend/.env` on purpose, so
opt in with the variable set for that one command, e.g.
`DATABASE_URL=postgresql+psycopg://yc:yc@localhost:5433/ycautoapply pytest -m integration`
(PowerShell: `$env:DATABASE_URL = "..."; pytest -m integration`).

## 6. The chain, in order

All commands are `python -m app.cli <command>`; `--help` on any of them
prints the full docstring.

| Step | Command | What it does / what to expect |
|---|---|---|
| 1 | `sync` | Fetches the yc-oss dataset and upserts every company matching India + Active + team size 1–200 (D25, §8). ~144 matched. Companies that stop matching are marked `status='out_of_band'` and skipped by later stages; they are restored if they match again. |
| 2 | `scrape` | Fetches each Active company's YC page (1 request/second, ~3 minutes) and stores founders (outreach rows start in `NEW`), job postings, news and launches. ~269 founders. A company whose page fails is reported and skipped; the rest continue. |
| 3 | `parse-resume PATH` | Sends your resume (`.pdf`, `.txt` or `.md`) to Gemini and stores structured claims — the only facts the drafter may assert about you. Re-runnable. |
| 4 | `show-claims` | Prints the stored claims. **Read every one.** A claim flagged `[span NOT found verbatim in resume]` is excluded from drafting unless you pass `--keep-unverified` later (ruling R3). |
| 5 | `resolve [--limit N]` | Resolves each founder's address (§9): a launch's published email first (D18), then generated candidates verified in priority order, stopping at the first `valid`; catch-all domains are never guessed. Commits after every founder, so Ctrl-C loses nothing already paid for. ~2.5 verifications per founder against a 100/day quota: use `--limit 40` per day. When the quota runs out the run pauses (not fails) and resumes next day. |
| 6 | `list-manual` / `set-email ID ADDRESS` | Roughly 30% of founders end in `NEEDS_MANUAL_EMAIL` (all candidates invalid, catch-all, no domain, or an unattributable published email). `list-manual` shows each with the candidates tried and their verdicts, the LinkedIn URL and any published-email hint. Pick or find the address and `set-email ID ADDRESS` (moves to `READY_TO_DRAFT`); `set-email ID --unresolved` gives up on that founder. |
| 7 | `draft --limit 5 --allow-free-tier` | First read-through only: five drafts on the free tier. **Read them** with `review --dry-run` before spending on the paid tier. |
| 8 | `draft [--limit N] [--keep-unverified]` | Drafts one enquiry per `READY_TO_DRAFT` founder (§10) with `GEMINI_TIER=paid`. Sources offered in priority order news > launch > intern-appropriate posting > long description (D17/D23). Each draft passes the deterministic validator or is retried once; a second failure lands in the queue flagged *needs manual write*. No research material at all → flagged without an LLM call. Three consecutive drafter failures abort the run. |
| 9 | `review [--dry-run] [--keep-unverified]` | Terminal approval queue (D5). For each `PENDING_APPROVAL` draft you see the company, founder, the cited source and the message **exactly as it will be sent** (greeting, body, signature, opt-out line — ruling R2). Keys: `[a]pprove [e]dit [d]raft again [r]eject [s]kip [q]uit`. `e` opens `$EDITOR` (first line = subject, rest = body) and re-runs the validator; `d` sends the row back for a fresh draft (the fix for a bad citation). Each action commits on its own. `--dry-run` only renders. Pass `--keep-unverified` if the drafts were made with it, so edits validate against the same claims. |
| 10 | `gmail-auth` | One-time OAuth consent in the browser (`gmail.send` scope only). Stores the refresh token in the `gmail_credentials` table, never in a file (§14). |
| 11 | `send --to-self [--limit ≤3]` | Smoke test: emails the first approved draft(s) to `GMAIL_SENDER_ADDRESS` instead of the founders. Reserves nothing, changes no state. Check spam placement, the attachment, the signature and the opt-out line. |
| 12 | `send [--limit N]` | Sends every `APPROVED` outreach: at most `DAILY_SEND_CAP` per rolling 24 hours, 20–75 s random pause between sends (§18), plain text with the resume attached. Each row is reserved by a committed conditional `UPDATE` before Gmail is called, so a crash can never send twice (§13). The first Gmail error stops the run. Run again the next day for the remainder. |

Phase 1 sends step 0 only (ruling R-C): follow-ups are Phase 2.

## 7. Quotas and cost

- **MyEmailVerifier**: 100 verifications/day on the free tier; the adapter
  self-throttles to 30 requests/minute. `resolve` pauses cleanly on
  exhaustion and picks up next day. Budget ~2.5 per founder.
- **Gemini**: free tier is for development only and sends no founder data
  without an explicit `--allow-free-tier`. Paid tier ≈ INR 95 per full run
  (§19); billing by UPI through Google Cloud Billing (D14).
- **Gmail**: 500 sends/day for a consumer account; `DAILY_SEND_CAP` defaults
  to 20. `--to-self` copies do not count toward the DB cap but do toward
  Gmail's.

## 8. Recovery notes

- **`needs manual check N`** from `send`: a message row has `sent_at` set
  but no `gmail_message_id` — a previous run crashed between reserving and
  recording. Look in the Gmail *Sent* folder. If it went out, set
  `gmail_message_id`/`gmail_thread_id` on the row and the outreach state to
  `sent` by hand; if not, clear `sent_at` and it will send next run. Such
  rows are never re-sent automatically.
- **`invalid_grant`** from `send`: the refresh token was revoked or
  expired. Re-run `gmail-auth`.
- **`draft` aborted after consecutive drafter failures**: usually a bad key
  or an exhausted Gemini quota. The reason (exception type only) is in
  `outreach.last_error`; the founders stay `READY_TO_DRAFT` and are retried
  on the next run.
- **`resolve` errors N**: a per-address verifier failure (5xx, transport)
  leaves that founder `NEEDS_EMAIL`; it is retried next run, and addresses
  already judged are never re-verified.
- **`scrape` error lines**: that company is skipped for this run; re-run
  `scrape` later (already-known founders keep their outreach state).

## 9. Opt-out line — deleted (2026-09-23)

Spec §18 asks for a single-line opt-out sentence in every email. There
isn't one: the operator's instruction was "delete this line from the
email", and it is his name on the letter. This is a deliberate departure
from the spec, not an oversight.

`OPT_OUT_LINE` in `app/domain/compose.py` is now `""`. The constant was
emptied rather than removed, and `compose_wire_text` drops empty parts,
so a letter now ends at the signature with no trailing blank line — and
putting a sentence back is that one string, nothing else. Because the
wire text is composed at send and preview time, this applied immediately
to letters that were already drafted.

## 10. Phase 1 status (superseded by §12)

This section originally listed Phase 1 debt deferred to Phase 2 (ruling
FR-4). Phase 2 is now implemented; §12 replaces the list below with the
current done/open split for the whole project. Kept for history: none of
it affected a Phase 1 run, and the two schema items it named
(`companies.last_synced_at`, `founders.scraped_at`) were populated by the
Phase 1 final fix wave before Phase 2 began.

## 11. Phase 2

Phase 2 adds a FastAPI app (`app/api/`) and a Vite/React frontend
(`../frontend`) over the same domain and application layers the CLI uses
— no logic is duplicated between them (spec §3, D20). Everything below
runs alongside the CLI; nothing in §1–10 changed behaviour.

### Jobs API

Every long-running stage — `sync`, `scrape`, `resolve`, `draft`,
`poll-replies`, `classify-replies`, `schedule-follow-ups`, `send` — runs
as a background job on its own thread and its own DB session, tracked in
the `jobs` table (`queued|running|succeeded|failed`). `POST
/api/jobs/{kind}` starts one and returns its id; a second `POST` for a
kind already running returns **409** with the running job's id, backed by
a partial unique index (`uq_jobs_kind_active`), not a disabled button.
`GET /api/jobs` is what the Pipeline screen polls (every 2s, only while a
job is running) to show live progress. A job that fails records the
**exception type name only** — never the message, which can carry a
secret — and a job left `running` when the API restarts is marked
`failed: interrupted` on the next boot, freeing its kind immediately.

`created_by` on each job row is `"ui"` (the default, a bare `POST` with
no body), `"schedule"` (§ below), or `"cli"` (reserved). See §7 of the
Phase 2 spec for the full route table.

On `GEMINI_TIER=free`, the `draft` and `classify-replies` jobs refuse to
start — the UI has no `--allow-free-tier` equivalent (unlike the CLI's
`draft` command, see §3), so both jobs fail immediately and the job row
shows `failed: RuntimeError` with no further detail (the exception
message, not shown, names which job refused). Set `GEMINI_TIER=paid` or
`LLM_PROVIDER=sarvam` before using either job from the UI.

### Reply loop

`poll-replies` uses Gmail's History API to fetch everything since the
last poll in one call (`gmail_credentials.last_history_id`), matches
inbound messages to our own threads by `gmail_thread_id`, and marks a
message from `mailer-daemon@`/`postmaster@` as a bounce (outreach state
`BOUNCED`, no classification, no follow-up). `classify-replies` sends
each remaining inbound message through the LLM configured by
`LLM_PROVIDER` for a structured label:

```
INTERESTED | REJECTED | REFERRAL | AUTO_REPLY | OUT_OF_OFFICE | UNCLEAR
```

`INTERESTED`/`REJECTED`/`REFERRAL`/`UNCLEAR` move the outreach to
`REPLIED` and cancel any pending follow-up. `AUTO_REPLY`/`OUT_OF_OFFICE`
are recorded and change nothing — an autoresponder can never end an
outreach on its own. Every label is shown with its one-line rationale and
is correctable in the UI in one click (`POST /api/messages/{id}/label`);
correcting a machine label back to a human one (or vice versa) re-applies
the same transition rules, including restoring a cancelled follow-up.
Replying itself is out of scope — the Thread screen links to the Gmail
thread and you answer there.

### Follow-ups

`schedule-follow-ups` finds `SENT` outreach rows whose `next_action_at`
has passed and `sequence_step < 2`, advances the step, and returns them
to `READY_TO_DRAFT` for the same drafter and the same approval queue used
for the first email. Step 1 fires 5 days after the previous send, step 2
after 12 (read from the `settings` table, live — changing the interval
affects pending rows immediately, not just future ones). After step 2
with no reply, the row closes (`CLOSED`). A follow-up draft carries the
full thread history, must be shorter than the message before it
(~60 words at step 1, ~40 at step 2), and is rejected by the validator if
it restates the earlier email instead of continuing it. Nothing
auto-sends at any step — the human approval gate is unchanged.

### Critic

A second LLM call (`app/application/critique_draft.py`) reviews every draft
after it passes the deterministic validator and returns a verdict
(`PASS` / `NEEDS_REVISION`) plus up to three one-line concerns, shown in
the Review Queue as a small dismissible note. It is advisory only — it
can never block a send (D22) — and a crashed or misconfigured critic
records `critic_verdict = "ERROR"` (never a fabricated `PASS`, never a
silent `NULL`) and drafting proceeds without it.

### Scheduled trigger (spec A1)

Reply polling and follow-up scheduling must happen with the browser
closed. `../scripts/schedule-tasks.ps1` registers two Windows Scheduled
Tasks, under your own user account, that `curl.exe`-POST the exact same
`/api/jobs/poll-replies` and `/api/jobs/schedule-follow-ups` endpoints the
UI's buttons call, with `created_by: "schedule"` — one code path per
stage regardless of who or what started it. See the script's own header
comment and the root `../README.md` quickstart for setup and what happens
if the API isn't running when a trigger fires (nothing — it fails
silently and the next trigger tries again).

### Frontend — the five screens

`../frontend`, Vite + React + TanStack Query, dense hand-written CSS
(spec §8: "a tool for reading 270 emails, not a marketing dashboard").

| Screen | Route | Purpose |
|---|---|---|
| Pipeline | `/` | Funnel counts by state, job buttons with live progress, quota banners |
| Review Queue | `/queue` | The primary workspace — approve/edit/redraft/reject/skip a draft against its cited source, keyboard-first (`a e d r s`, `j/k`) with visible buttons alongside |
| Contacts | `/contacts` | All companies/founders, filterable; the hiring filter is a view control, never a pipeline gate |
| Thread | `/thread/:id` | Per-outreach timeline — every outbound message, every reply with its label and rationale, a correction control, a Gmail deep link |
| Manual email | `/manual` | `NEEDS_MANUAL_EMAIL` founders — candidates tried, verdicts, set an address or give up |

### Gmail scope (spec A2)

`gmail-auth` now requests both `gmail.send` and `gmail.readonly` (Phase 2
needs the read scope to poll replies). It does not check what scopes an
existing token has — it always runs Google's consent flow and replaces
the stored token. If your stored token predates Phase 2, re-run it once,
same command as before: `python -m app.cli gmail-auth`.

### Auth (spec A3)

The API has no login and binds to `127.0.0.1` only. This is deliberate,
not a gap — see the root `../README.md` for why, and do not put it behind
a tunnel or reverse proxy.

## 12. Status: done vs still open

**Done** (Phase 1 + Phase 2, both implemented and offline-tested):

- The full CLI pipeline (§1–9): sync, scrape, resolve, draft, review,
  send, plus `gmail-auth` requesting both scopes.
- The Jobs API, all seven stage kinds, crash recovery, the 409
  double-start guard, `created_by` tracking (§11).
- Reply polling (History API + `newer_than:14d` fallback), bounce
  detection, reply classification, one-click label correction with
  transition rules, follow-up scheduling through `CLOSED` (§11).
- The voice critic, advisory-only, with the `ERROR` verdict for a
  crashed/misconfigured provider (§11).
- All five frontend screens, keyboard-driven review queue with parity
  buttons, optimistic updates (§11).
- The scheduled trigger (`scripts/schedule-tasks.ps1`) for unattended
  polling and follow-ups (§11, spec A1).
- `companies.last_synced_at` and `founders.scraped_at` are populated.

**Deliberately out of scope** (spec §11 — not bugs, not planned):

- Replying to founders from inside the app — the Thread screen links to
  Gmail; you answer there.
- Job cancellation — jobs are minutes long and commit incrementally, so
  killing the API loses nothing already paid for.
- Multi-user, authentication, or hosting beyond `127.0.0.1`.
- The seven-worker-process architecture Phase 1 originally specified
  (D20) — one laptop, one process, is enough.
- Bounce *parsing* beyond sender detection (soft vs. hard).

**Still genuinely unused** (present in the schema, no caller — carried
forward from Phase 1, unaffected by Phase 2):

- `companies.regions`, `companies.all_locations`, `companies.stage`,
  `job_postings.role_specific_type`/`salary_range`/`skills`/`raw`,
  `company_news.raw`, `outreach.attempts`, `outreach.locked_at`.
- `OutreachRepo.list_by_state` / `MessageRepo.list_by_state`;
  `EmailVerifier.remaining_quota`.
- The transient-retry `attempts > 5 → FAILED` path and the stale
  `locked_at` reclaim (§13 of the Phase 1 spec) — Phase 2's job model
  (own session, per-unit commits, startup recovery) covers the same
  crash-safety need for jobs; these two Phase 1-specific mechanisms were
  never built.
- Style references from previously edited messages (Phase 1 spec §8-6)
  and the ETag cache / persisted parse-failure retry flag in `scrape`
  (Phase 1 spec §8-2).
- Validator gaps accepted in review: sentence splitting on abbreviations,
  an emoji range that is not exhaustive, and the subject all-caps rule
  firing only from two capitalised words.

**Not yet done**: Task 20 (end-to-end verification and the final
whole-suite gate) is the remaining item in the Phase 2 plan — this README
describes what is implemented and tested offline, not a post-Task-20
sign-off.
