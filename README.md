# YC Auto Apply

A single-user, single-laptop tool that finds YC-funded companies hiring
interns in India, drafts a grounded outreach email to each founder from
your resume and their public research material, and puts every draft in
front of you before anything sends. Phase 2 adds reply polling,
follow-ups and a browser UI on top of the Phase 1 CLI pipeline; nothing
sends, labels, or replies to anything without a human clicking approve.

Design specs (binding):
- `docs/superpowers/specs/2026-09-03-yc-auto-apply-design.md` (Phase 1)
- `docs/superpowers/specs/2026-09-13-yc-auto-apply-phase-2-design.md` (Phase 2)

Detailed operator guide: `backend/README.md` — read it before your first
real run. This file is the quickstart plus the two things you must not
skip.

## Read this before you do anything else

**The API has no authentication and binds to `127.0.0.1` by design**
(spec A3). There is no login screen because a login screen on a
single-user local app is theatre — binding to localhost is the real
control. Anyone who can reach port 8000 on your machine can start jobs,
read founder data and trigger a send. **Never** put this behind a
reverse proxy, a tunnel (ngrok, Cloudflare Tunnel, etc.), `0.0.0.0`, or
any port forward. If you ever want to expose it beyond your own laptop,
that is a new design decision this project has not made.

**Free tiers first.** MyEmailVerifier's free tier gives 100
verifications/day; Gemini's free tier gives 20 requests/day per model.
On `GEMINI_TIER=free` the CLI's `draft` command refuses to run unless
you pass `--allow-free-tier` — and even then it *does* send founders'
names, titles and bios to the free-tier key (`backend/README.md` §3).
The UI is stricter: the Pipeline's `Draft` and `Classify replies` jobs
have no `--allow-free-tier` flag and always refuse to start on
`GEMINI_TIER=free` — the job row shows `RuntimeError` with no further
detail. Set `GEMINI_TIER=paid` or `LLM_PROVIDER=sarvam` before using
those two jobs from the UI. See `backend/README.md` §3, §7 and §11 for
the full cost picture before switching anything to a paid tier.

## Phase 2 quickstart

Prerequisites: Python 3.11, Node (for the frontend), Docker (for
Postgres), a Gmail account with a Google Cloud OAuth "Desktop app"
client, a MyEmailVerifier key, and either a Gemini or Sarvam key. Full
detail, including every `.env` variable, is in `backend/README.md` §1–3.

From the repo root, in order:

```bash
cd backend
docker compose up -d              # Postgres on localhost:5433
python -m venv .venv              # first time only
pip install -e ".[dev]"           # first time only (activate the venv first — see below)
cp .env.example .env              # first time only — then fill it in
# .env.example's DATABASE_URL defaults to the native-Postgres port (5432).
# Since the command above starts Docker's Postgres instead, set in .env:
#   DATABASE_URL=postgresql+psycopg://yc:yc@localhost:5433/ycautoapply
python -m alembic upgrade head
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Activate the venv before `pip install` and before running anything below
— pick the line for your shell (do not use `&&`, it is a parse error in
Windows PowerShell 5.1):

```powershell
.venv\Scripts\Activate.ps1        # PowerShell
```

```bash
source .venv/Scripts/activate     # Git Bash
```

Leave that `uvicorn` process running — the API must stay up for the UI
and the scheduled tasks below to do anything. In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The dev server prints a `localhost:5173` URL — that's the app (Pipeline,
Review Queue, Contacts, Thread, Manual email).

**One-time, only if you set up Gmail under Phase 1**: Phase 2 needs
`gmail.readonly` in addition to Phase 1's `gmail.send` (spec A2), so
reply polling can read your mailbox — it never labels, deletes or
modifies anything. A send-only token from before cannot read mail; Google
enforces scopes on the token, not on the request. Re-run this once (in
the `backend` terminal, with the venv active):

```bash
python -m app.cli gmail-auth
```

`gmail-auth` always runs Google's consent flow and replaces the stored
token with a fresh one covering both scopes — it does not check what the
old token already had. Re-running it once is enough; after that you can
ignore this step.

**Scheduled polling and follow-ups.** The browser being closed must not
stop replies from being noticed or follow-ups from going out (spec A1).
With the API running, register two Windows Scheduled Tasks under your
own user account (no admin rights needed):

```powershell
.\scripts\schedule-tasks.ps1
```

This registers `poll-replies` (every 30 minutes — a deliberate choice,
not a spec requirement; spec A1's "once a day" is only an example) and
`schedule-follow-ups` (daily at 9am), each calling the exact same
`POST /api/jobs/<kind>` endpoint the UI's own buttons call — one code
path per stage, whether a human or the scheduler started it. Re-run the
script any time to replace the tasks; `.\scripts\schedule-tasks.ps1
-Unregister` removes them. See the comment block at the top of the
script for how it fails (harmlessly) when the API isn't running.

**Classification is not scheduled.** `poll-replies` only fetches replies
and marks bounces; it never calls the LLM. `classify-replies` is a
separate job that spends LLM calls, so it is left for you to trigger —
open the Pipeline screen and click **Classify** when you want replies
labelled.

## Repo layout

```
backend/   FastAPI app, CLI, domain logic, Postgres migrations, tests
frontend/  Vite + React UI (Pipeline, Review Queue, Contacts, Thread, Manual email)
scripts/   schedule-tasks.ps1 — Windows Task Scheduler setup for A1
docs/      design specs and SDD process artifacts
```

## Tests

```bash
cd backend
python -m pytest -q                        # offline, no DB, no network
python -m pytest -m integration            # needs DATABASE_URL
```

```bash
cd frontend
npm test
```
