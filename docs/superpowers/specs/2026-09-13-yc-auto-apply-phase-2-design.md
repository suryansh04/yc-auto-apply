# YC Auto Apply — Phase 2 Design Spec

Frontend, replies, follow-ups and outcome tracking.

Phase 1 (branch `phase-1-pipeline`, PR #1) delivered the CLI pipeline:
sync → scrape → resolve → draft → review → send, 608 offline tests, running
live against Postgres with MyEmailVerifier and Sarvam. This spec covers what
the original design (`2026-09-03-yc-auto-apply-design.md`) deferred to
Phase 2, and amends three of its decisions where live experience contradicted
them.

---

## 1. Goal

Run the entire outreach from a browser, and know what came back.

Concretely: start every pipeline stage from a button, review and approve
drafts with the keyboard, send, then see each founder's reply, its
classification and the outcome — without opening a terminal.

Phase 1's CLI keeps working and stays the fallback; the API is a second
adapter over the same use cases, not a replacement.

---

## 2. Amendments to the Phase 1 spec

| Ref | Phase 1 said | Phase 2 says | Why |
| --- | --- | --- | --- |
| **D20** | Seven single-responsibility worker processes, one per stage | **No workers.** Stages run as API-triggered background jobs in the FastAPI process | One user, one laptop. Seven always-on containers buy isolation nobody is paying for. A job that must run unattended (reply polling, follow-up scheduling) is handled by A1 below, not by a resident worker. |
| **§17** | `docker compose up` starts Postgres, API and seven workers — nine containers | Postgres in Docker; API and frontend run directly (`uvicorn`, `npm run dev`) | Follows from D20. |
| **R-C** (Phase 1 ruling) | `SendApproved` raises `NotImplementedError` for `sequence_step != 0` | Follow-up steps 1 and 2 are supported | That guard existed precisely so Phase 2 would have to come here deliberately. §6 lifts it. |

**A1 — scheduled trigger.** Reply polling and follow-up scheduling must happen
when the browser is closed. A Windows Scheduled Task calls
`POST /api/jobs/poll-replies` and `POST /api/jobs/schedule-follow-ups` once a
day. Setup is one documented `schtasks` command; the endpoints are the same
ones the UI buttons call, so there is exactly one code path per stage.

**A2 — Gmail scope.** Phase 1 authorised `gmail.send` only. Reply polling needs
`gmail.readonly`, which requires re-running `gmail-auth` once. Read-only is
deliberate: the app never modifies, labels or deletes mail.

**A3 — API auth: none.** Uvicorn binds `127.0.0.1`. A login screen on a
single-user local app is theatre; binding to localhost is the actual control.
If the API is ever exposed beyond the laptop, that is a new design decision,
and this line is the reminder that it was never made here.

**A4 — LLM provider.** The critic (D22) and the reply classifier use
`Settings.llm_provider` like every other LLM call — Sarvam today. Gemini's free
tier (20 requests/day) cannot support them.

---

## 3. Architecture

```
app/
  domain/          unchanged, pure, adapter-free
  application/     use cases (+ new ones below), adapter-free
  adapters/        yc_oss, yc_page, email_verifier, gemini, sarvam, gmail, db
  api/             NEW — FastAPI routers, DTOs, job runner
  cli.py           unchanged, still supported
frontend/          NEW — Vite + React (JSX) + TanStack Query
```

`api/` is **a third adapter, peer to `cli.py`**. The layering rule is
unchanged and still enforced by test: `domain/` and `application/` never import
`adapters/` or `api/`. A router may import a use case and a repo; it may not
contain a business rule. Any logic that would otherwise live in a router
belongs in a use case where the CLI can reach it too.

This is what makes Phase 2 cheap: `ReviewOutreach`, `SendApproved`,
`ResolveManualEmail`, `DraftOutreach`, `SyncCompanies`, `ScrapeCompanyPage` and
`ResolveEmails` already exist, already have no CLI coupling, and already have
tests. The API wraps them.

### New application use cases

| Use case | Responsibility |
| --- | --- |
| `PollReplies` | Fetch new inbound mail via Gmail History, match to threads, persist inbound `messages`, flag bounces |
| `ClassifyReply` | Label one inbound message (LLM), persist label/confidence/rationale, transition on human labels |
| `ScheduleFollowUps` | Move due `SENT` rows to the next sequence step, or to `CLOSED` after step 2 |
| `RunJob` | Record a job row, run a stage on a background thread, record progress and outcome |

---

## 4. Jobs

Every long stage (`sync`, `scrape`, `resolve`, `draft`, `poll-replies`,
`classify-replies`, `schedule-follow-ups`, `send`) runs as a job.

**Table `jobs`** (migration 0003): `id`, `kind`, `status`
(`queued|running|succeeded|failed`), `started_at`, `finished_at`, `done`,
`total`, `summary`, `error`, `created_by` (`ui|schedule|cli`).

**One job per kind at a time**, enforced by a partial unique index on
`kind WHERE status IN ('queued','running')` — a double-clicked button loses to
a database constraint, not to a disabled attribute in the DOM. `POST` on a kind
already running returns **409** with the running job's id.

**Threading.** Jobs run on a background thread with their **own SQLAlchemy
Session**, never the request's. A job updates `done`/`total` as it progresses so
the UI shows real movement, not a spinner. On an unhandled exception the job
row records `failed` with the exception **type name only** — the same rule the
adapters follow, because a traceback can carry an API key.

**Crash recovery.** A job left `running` when the API restarts is stale. On
startup, any `running` job older than its kind's timeout is marked `failed`
with `"interrupted"`, which also releases the unique index.

**Cancellation** is out of scope. Jobs are minutes long and commit
incrementally (Phase 1 ruling FR-3), so killing the API loses nothing already
paid for.

---

## 5. Replies

### Fetching

Gmail's **History API** is the mechanism: `gmail_credentials` gains
`last_history_id`, and each poll asks for everything since it. One call covers
all threads; the naive alternative (fetch each of ~279 threads) would cost 279
calls per poll and hit rate limits for no benefit.

History ids expire after roughly a week. When Gmail rejects a stale id (404),
`PollReplies` falls back to `users.messages.list q="newer_than:14d"`, which is
strictly a superset of anything that could have been missed, then resumes
incremental polling from the new id.

**Matching.** An inbound message is ours when its `threadId` equals a
`gmail_thread_id` we stored when sending. Everything else is ignored — the app
reads the whole mailbox but records only its own threads.

**Idempotency.** `gmail_message_id` is unique on `messages`. Re-polling the same
window is therefore free, which is what makes the coarse fallback safe.

**Bounces.** A message from `mailer-daemon@` or `postmaster@` on one of our
threads moves the outreach to `BOUNCED` and is never sent to the classifier.
The founder's `resolved_email` is marked dead so no follow-up is attempted.

### Classifying

One LLM call per inbound message, structured output:

```
{ label: "INTERESTED" | "REJECTED" | "REFERRAL" | "AUTO_REPLY" | "OUT_OF_OFFICE" | "UNCLEAR",
  confidence: number,        // 0-1
  rationale: string }        // one line, <= 25 words
```

Stored on the inbound `messages` row and **always displayed**. Every label is
correctable in the UI in one click; a correction overwrites the label, keeps the
model's original in `rationale`, and applies the same transition rules. A wrong
label must never be silently load-bearing.

**Transitions.** `INTERESTED`, `REJECTED`, `REFERRAL`, `UNCLEAR` → outreach
`REPLIED`, follow-ups cancelled (`next_action_at = NULL`). `AUTO_REPLY` and
`OUT_OF_OFFICE` are recorded and change nothing — an autoresponder must not be
able to end an outreach.

### Replying

Out of scope, deliberately. The Thread screen links to the Gmail thread and you
answer there, in a real mail client. The resume PDF is attached to the *first*
email already, so the common "send me your resume" round trip does not arise.

---

## 6. Follow-ups

`ScheduleFollowUps` selects `SENT` outreach rows where `next_action_at <= now`
and `sequence_step < 2`, increments `sequence_step`, and returns them to
`READY_TO_DRAFT`. Step 1 fires at +5 days, step 2 at +12, both read from the
`settings` table (which Phase 1 created and never used). After step 2 with no
human reply, the row moves to `CLOSED`.

Drafting a follow-up differs from a first email in three ways:

1. The prompt carries the **full thread history**, so it can refer to the
   earlier note without repeating it.
2. It **must be shorter than the previous message** — a new deterministic
   validator rule. Roughly 60 words at step 1, 40 at step 2. A follow-up earns
   less attention than the first email did, not more.
3. The one-liner-overlap rule extends to the previous message: a follow-up that
   restates the first email is rejected.

Follow-ups go through the **same approval queue and the same validator**.
Nothing auto-sends at any step; the human gate from D5 is unchanged.

The `uq_messages_outreach_step_outbound` index already scopes one outbound row
per step, and reserve-before-send (Phase 1 ruling R1) works per step unchanged.
`SendApproved`'s `NotImplementedError` guard is lifted, and the tests that
pinned it become tests of the real behaviour.

---

## 7. API

All routes under `/api`, bound to `127.0.0.1`. Each is a thin translation:
DTO in → use case → DTO out.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/stats` | Funnel counts by state, verifier quota, last job per kind |
| GET | `/queue` | Pending drafts in `rank_key` order, with cited source, critic note, composed preview |
| POST | `/outreach/{id}/approve` | Approve (stamps `approved_at`) |
| POST | `/outreach/{id}/edit` | Edit subject/body; returns validator errors, persists nothing on failure |
| POST | `/outreach/{id}/reject` | → `DISCARDED` |
| POST | `/outreach/{id}/redraft` | → `READY_TO_DRAFT` |
| GET | `/manual` | `NEEDS_MANUAL_EMAIL` founders with candidates tried and verdicts |
| POST | `/outreach/{id}/email` | Set a manual address, or give up → `UNRESOLVED` |
| POST | `/send` | Send approved, daily cap enforced |
| GET | `/contacts` | Companies and founders, filterable by state / hiring / batch / email status |
| GET | `/outreach/{id}/thread` | Outbound + inbound timeline, labels, Gmail deep link |
| POST | `/messages/{id}/label` | Correct a classification |
| POST | `/jobs/{kind}` | Start a job; 409 if that kind is running |
| GET | `/jobs` | All job statuses (UI polls this) |

Validation errors from `edit` return **422 with the validator's own messages**,
verbatim — the reviewer sees exactly what the CLI reviewer sees.

---

## 8. Frontend

Vite + React (JSX) + TanStack Query. Four screens, per Phase 1 §16.

- **Pipeline** — funnel counts, job buttons with live progress, quota banners,
  recent failures.
- **Review Queue** — the primary workspace. Draft on the left, company/founder
  and the *specific* cited source row on the right, so a claim is checkable at a
  glance. Critic verdict as one small dismissible line — a second opinion, never
  a gate. Keyboard: `a` approve, `e` edit, `d` redraft, `r` reject, `s` skip,
  `j/k` move. Optimistic updates, because ~270 drafts at one round trip each is
  a bad afternoon.
- **Contacts** — all companies and founders, filterable. The hiring filter is a
  view control, never a pipeline gate (D19).
- **Thread** — per-outreach timeline: every outbound message, every reply, the
  label with its rationale, a correction control, and a Gmail link.

**Visual direction.** Dense and information-first: this is a tool for reading
270 emails, not a marketing dashboard. Hand-written CSS, system font stack, one
accent colour, real tables. No card grids, no gradient headers, no component
library's default look.

**State.** TanStack Query owns server state; no Redux. Job progress polls every
2s **only while a job is running**.

---

## 9. Error handling

Unchanged from Phase 1 where it applies, plus:

- A failed job records the exception **type name** and leaves the pipeline row
  untouched; nothing is half-committed, because every stage already commits per
  unit of work.
- Gmail 401 during polling → the job fails with "re-run gmail-auth", which the
  UI surfaces as a banner rather than a silent zero-result poll.
- A classification failure leaves the inbound message unlabelled and visible in
  the UI as "unclassified" — never guessed at, never dropped.
- The API never returns a raw traceback. 500s carry a request id; the detail
  goes to the log.

---

## 10. Testing

The Phase 1 definition of done is unchanged and extended.

**Backend:** `cd backend && pytest` passes with **no env vars and no network**.
API tests use FastAPI's `TestClient` against the existing in-memory `FakeRepos`
— no Postgres, no Gmail, no LLM. Job tests run the runner with a fake stage
function and assert the `jobs` row transitions, the 409 on a second start, and
the stale-job recovery on startup.

**Reply tests:** Gmail History responses mocked with `respx`, including the
404-stale-history fallback, a duplicate message (must not double-insert), a
bounce, and an autoresponder (must not cancel follow-ups).

**Frontend:** Vitest + React Testing Library, API mocked with MSW. The tests
worth writing are the review screen's keyboard dispatch, optimistic update and
rollback on failure, and that the composed preview — not the bare body — is what
is shown.

**Definition of done:** `pytest` green offline **and** `npm test` green.

---

## 11. Out of scope

Deliberately not in Phase 2:

- Replying to founders from inside the app (§5).
- Job cancellation (§4).
- Multi-user, authentication, hosting beyond localhost (A3).
- The seven worker processes (§2, amending D20) — revisit only if one laptop stops
  being enough.
- Bounce *parsing* beyond sender detection: a bounce marks the row, it does not
  try to distinguish soft from hard.

---

## 12. Success criteria

1. Every pipeline stage can be started from the browser and shows real progress.
2. ~270 drafts can be reviewed with the keyboard without touching the mouse.
3. A reply arriving in Gmail appears in the app, labelled with a rationale,
   within a day, with follow-ups cancelled automatically.
4. Follow-ups at +5 and +12 days appear in the approval queue on time, shorter
   than the message before them, without the browser being open.
5. `pytest` offline and `npm test` both green.
6. The CLI still does everything it did in Phase 1.
