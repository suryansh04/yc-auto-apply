# YC Auto Apply — Phase 1 operator guide

Phase 1 is a single-process command-line pipeline: sync the target YC
companies, scrape their founders and research material, resolve founder
email addresses, draft one grounded internship enquiry per founder, review
every draft by hand in the terminal, send the approved ones through Gmail.
Everything runs from this `backend/` directory against a Postgres database.
There is no dashboard, no worker loop and no follow-up sending in Phase 1
(see *Phase 2 debt* at the end).

Design spec: `../docs/superpowers/specs/2026-09-03-yc-auto-apply-design.md`
(section numbers below refer to it).

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
.venv/Scripts/activate          # Windows; `source .venv/bin/activate` elsewhere
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
pytest -m integration       # 9 DB tests; skipped when DATABASE_URL is not set
```

The offline suite runs against in-memory fakes and covers the domain rules,
every use case, the CLI wiring and an end-to-end run of the whole chain.
The integration suite exercises the real repositories and constraints
against your Postgres (run `alembic upgrade head` first); it has not yet
been run against a live database on this branch.

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

## 9. Opt-out line — needs sign-off

Spec §18 requires a single-line opt-out sentence in every email but does
not fix its wording. The wording lives in one constant,
`OPT_OUT_LINE` in `app/domain/compose.py`:

> If you'd rather not hear from me again, reply with a quick "no" and I
> won't follow up.

It is composed at send time under the signature and shown in `review`.
**Sign off on (or change) this wording before the first real `send`**
(ruling R-B / FR-6).

## 10. Phase 2 debt

Recorded here rather than removed (ruling FR-4). None of it affects a
Phase 1 run.

- Unused §7 columns, present in the schema but never written:
  `companies.regions`, `companies.all_locations`, `companies.stage`,
  `job_postings.role_specific_type`, `salary_range`, `skills`, `raw`,
  `company_news.raw`, `outreach.attempts`, `outreach.locked_at`.
  (`companies.last_synced_at` and `founders.scraped_at` are populated since
  the final fix wave.)
- The `settings` table (`send_window_*`, `followup_day_*`) is never read:
  there is no send window, and `followup_day_1` is a constant in
  `send_approved.py`.
- `OutreachRepo.list_by_state` / `MessageRepo.list_by_state` have no Phase 1
  caller; `EmailVerifier.remaining_quota` is never called.
- Follow-ups (steps 1–2), reply detection, bounce handling (`BOUNCED`),
  the transient-retry `attempts > 5 → FAILED` path and the stale
  `locked_at` reclaim (§13) are not implemented.
- The voice critic (D22), style references from previously edited messages
  (§8-6), the ETag cache and the persisted parse-failure retry flag in
  `scrape` (§8-2) are not implemented.
- The dashboard (§16) and worker loops (§17) do not exist; the CLI is the
  only interface.
- Validator gaps accepted in review: sentence splitting on abbreviations,
  an emoji range that is not exhaustive, and the subject all-caps rule
  firing only from two capitalised words.
