# YC Auto Apply

Cold-email YC founders for an internship, without sending anything you
have not read.

It finds YC companies in India, researches each company and founder on
the web, drafts one email per founder from facts it has checked, and puts
every draft in front of you. Nothing is sent until you press **Approve**.

<!-- A screenshot of the Review screen belongs here. -->

## What it does

1. **Sync** — pulls the YC company list and keeps the Indian ones.
2. **Scrape** — gets founders, news and launches from each company page.
3. **Resolve** — guesses the founder's email and verifies it is real.
4. **Research** — reads their YC page, launches, news and the founder's
   bio, and searches the web. Every fact is checked against its source
   and kept with its link. Old facts are marked so the email says
   "I read that ...".
5. **Draft** — writes the one paragraph about the company (plus an
   optional line about the founder). Your intro, your work, your
   projects and the ask are fixed text the model never touches.
6. **Review** — you read, edit and approve. The facts and their source
   links sit next to each letter, and problems are highlighted.
7. **Send** — goes out from your own Gmail, with your resume attached.
8. **Follow-ups** — sent emails with no reply show up when a follow-up is
   due (day 5, then day 12). Pick one of three suggestions, edit it,
   approve it, and it goes as a reply in the same thread.

## Before you start

- **Keep it on your laptop.** The API has no login and listens on
  `127.0.0.1` on purpose. Do not put it behind a tunnel, a proxy or
  `0.0.0.0`.
- **The model is Gemini 3.8 Flash on a paid key.** About ₹1.5 per letter
  (₹3 from January 2027), so roughly ₹50 for 30 letters. On a free key
  Google may train on what you send, so the app refuses to draft on one.
  Billing is set up in Google AI Studio.
- **Other free tiers.** Email verification gives 100 checks/day. Tavily
  web search gives 1,000 searches/month, about 4 per company.

## Setup

You need Python 3.11, Node 20+, and Docker Desktop (running).

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
these lines:

```ini
DATABASE_URL=postgresql+psycopg://yc:yc@localhost:5433/ycautoapply
EMAIL_VERIFIER_API_KEY=your-myemailverifier-key
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-key
GEMINI_TIER=paid
TAVILY_API_KEY=tvly-your-key
SIGNATURE=Your Name
RESUME_PATH=resume/resume.pdf
GMAIL_SENDER_ADDRESS=you@gmail.com
GMAIL_CLIENT_SECRETS_PATH=C:/full/path/to/client_secret.json
```

Notes:

- The database port is **5433**, not the 5432 in the example file — 5433
  is the Docker database you just started.
- `RESUME_PATH` can be relative to `backend/`, like above, or a full
  path. Leave it blank and it means `backend/resume/resume.pdf`, which
  is where the Upload button in the app puts your PDF. That folder is
  git-ignored.
- The two `GMAIL_` lines can stay blank for now. The app's own setup
  guide tells you what goes there.
- Get a Gemini key at [aistudio.google.com](https://aistudio.google.com)
  and turn on billing for it (paid tier).
- `TAVILY_API_KEY` is the web search. Get a free key at
  [tavily.com](https://tavily.com) (no card). Without it, research uses
  only what is already in the database (YC page, launches, news, bios),
  and the letters are less specific.
- Leave the rest alone. `GEMINI_MODEL` (`gemini-3.8-flash`) and
  `DAILY_SEND_CAP` already have working defaults. Sarvam still works if
  you set `LLM_PROVIDER=sarvam` and `SARVAM_API_KEY`.

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
2. **My resume** — upload your PDF. It is read as soon as it is
   uploaded, because drafts may only say things your resume actually
   says.
3. **Pipeline** — press **Sync**, then **Scrape**, **Resolve**,
   **Draft**, in that order.
4. **Review** — read each letter and its **Research** card, edit what you
   want, press **Approve**. If the email says you read something, open
   that link before sending.
5. **Approved** — press **Send**.
6. **Follow-ups** — press **Check inbox for replies** first, then follow
   up on the ones that are due.

Everything after that is the same loop: draft, review, send, follow up.

## When something goes wrong

- **"invalid_grant" or sending stops working** — Google's login for the
  app expired. Apps in Google's "testing" mode lose it after 7 days.
  Reconnect Gmail from the app's setup page.
- **"No module named ..." when starting the API** — another Python is
  first on your PATH. Use the virtual environment from step 2, or the
  Python 3.11 you installed the packages with.
- **The app cannot reach the database** — start Docker Desktop, then
  `docker compose up -d` in `backend`.

## Keeping it running in the background

Replies should be noticed even when the app is closed. With the API
running:

```powershell
.\scripts\schedule-tasks.ps1
```

This adds two Windows scheduled tasks: reply polling every 30 minutes
and follow-up scheduling daily at 9am. Follow-ups still never send on
their own; they wait for you on the Follow-ups screen. Remove them with
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
