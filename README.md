# YC Auto Apply

Cold-email YC founders for an internship, without sending anything you
have not read.

It finds YC companies in India, drafts one email per founder from your
resume and their own public news, and puts every draft in front of you.
Nothing is sent until you press **Approve**.

<!-- A screenshot of the Review screen belongs here. -->

## What it does

1. **Sync** — pulls the YC company list and keeps the Indian ones.
2. **Scrape** — gets founders, news and launches from each company page.
3. **Resolve** — guesses the founder's email and verifies it is real.
4. **Draft** — writes one letter per founder, grounded in your resume.
5. **Review** — you read, edit and approve. Bad claims are highlighted.
6. **Send** — goes out from your own Gmail, with your resume attached.
7. **Replies** — checks for answers and drafts follow-ups.

## Before you start

- **Keep it on your laptop.** The API has no login and listens on
  `127.0.0.1` on purpose. Do not put it behind a tunnel, a proxy or
  `0.0.0.0`.
- **Free tiers are limited.** Email verification gives 100 checks/day.
  Gemini's free tier gives 20 requests/day and the app refuses to draft
  on it, because drafting would send founder data to a free key. Use
  `LLM_PROVIDER=sarvam` or a paid Gemini key for drafting.

## Setup

You need Python 3.11, Node 20+, and Docker.

**1. Start the database**

```bash
cd backend
docker compose up -d
```

**2. Install the backend**

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # PowerShell. Git Bash: source .venv/Scripts/activate
pip install -e ".[dev]"
```

Keep this virtual environment active in every terminal where you run
`python` or `pip`.

**3. Add your settings**

```bash
cp .env.example .env
```

That copy already has every setting in it. Open `backend/.env` and change
these eight lines:

```ini
DATABASE_URL=postgresql+psycopg://yc:yc@localhost:5433/ycautoapply
EMAIL_VERIFIER_API_KEY=your-myemailverifier-key
LLM_PROVIDER=sarvam
SARVAM_API_KEY=your-sarvam-key
SIGNATURE=Your Name
RESUME_PATH=resume/resume.pdf
GMAIL_SENDER_ADDRESS=you@gmail.com
GMAIL_CLIENT_SECRETS_PATH=C:/full/path/to/client_secret.json
```

Notes:

- The database port is **5433**, not the 5432 in the example file — 5433
  is the Docker database you just started.
- `RESUME_PATH` can be relative to `backend/`, like above, or a full
  path. The folder `backend/resume/` is already git-ignored.
- The two `GMAIL_` lines can stay blank for now. The app's own setup
  guide tells you what goes there.
- Leave the rest alone. `SARVAM_MODEL`, `DAILY_SEND_CAP` and the
  `GEMINI_` lines already have working defaults, and you only need a
  Gemini key if you set `LLM_PROVIDER=gemini`.

**4. Create the tables and run the API**

```bash
python -m alembic upgrade head
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Leave it running.

**5. Run the app**

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the URL it prints (usually http://localhost:5173).

## First run

In the app, in this order:

1. **Connect Gmail** — open the page and follow **First time? Set up the
   Google side**. It walks you through Google Cloud, takes about 10
   minutes, and is only needed once. It ends with a
   `client_secret.json`, the file `GMAIL_CLIENT_SECRETS_PATH` points at.
2. **My resume** — upload your PDF, then press **Re-read my resume**.
   Drafts can only say things your resume actually says, so this has to
   happen before drafting.
3. **Pipeline** — press **Sync**, then **Scrape**, **Resolve**,
   **Draft**, in that order.
4. **Review** — read each letter, edit what you want, press **Approve**.
5. **Approved** — press **Send**.

Everything after that is the same loop: draft, review, send.

## Keeping it running in the background

Replies should be noticed even when the app is closed. With the API
running:

```powershell
.\scripts\schedule-tasks.ps1
```

This adds two Windows scheduled tasks: reply polling every 30 minutes
and follow-up scheduling daily at 9am. Remove them with
`.\scripts\schedule-tasks.ps1 -Unregister`.

Reply classification is not scheduled, because it costs model calls.
Press **Classify replies** on the Pipeline when you want it.

## Tests

```bash
cd backend && python -m pytest -q      # offline, no database needed
cd frontend && npm test
```

## Layout

```
backend/    FastAPI app, CLI, domain logic, migrations, tests
frontend/   React UI
scripts/    Windows scheduled tasks
docs/       design specs
```

## More detail

`backend/README.md` is the long version: every setting, every command,
costs, and what to do when a step fails.
