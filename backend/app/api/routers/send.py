"""Task 5: `POST /api/send` - the browser peer of `app.cli`'s `send`.
Both drive the exact same `SendApproved` use case
(`app/application/send_approved.py`): reserve-before-send, compose-at-
send-time, the rolling 24h `daily_send_cap` counted by
`SendApproved.run` itself. This router holds no rule of its own - the cap
is enforced entirely inside `run`, and the Gmail config guard is entirely
`app.api.deps.require_send_config` (shared with `app.cli.send` so the two
callers cannot drift on what "configured enough to send" means).

`SendApproved.run` commits its own reservation and record steps as it
goes (see that module's docstring - each send is reserved in one
committed transaction, then recorded in another): unlike `queue.py`'s
write routes, this router never calls `repos.session.commit()` itself -
there is nothing left uncommitted on a normal return, and a raised
exception leaves exactly what the use case's own docstring says it
leaves (the reservation, surfaced next run as `needs_manual_check`).

**Safety - nothing here can send a real email during tests.** Two
independent things would each have to fail for that to happen:

1. `repos.sender` must be `None` before this route will even attempt to
   build a real `GmailSender` - `Repos.sender` (`app/api/deps.py`)
   defaults to `None` and is never set to anything else by `get_repos`;
   the ONLY way it is non-`None` is the `fake_sender` fixture
   (`backend/tests/conftest.py`) setting it directly on the `FakeRepos`
   instance the test's `client` fixture is built from.
2. Even if a test forgot `fake_sender`, building a real sender needs
   `get_settings()` (which raises before this line runs at all: the
   offline suite's `env -i` has no `DATABASE_URL`, a required field with
   no default) and then `require_send_config` to pass (SIGNATURE,
   RESUME_PATH, GMAIL_SENDER_ADDRESS, GMAIL_CLIENT_SECRETS_PATH, none of
   which are set) and then a stored refresh token
   (`GmailCredentialRepo`, a DB query the offline suite has no database
   for). Every one of those fails closed with no network call ever
   attempted.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Repos, get_repos, require_send_config
from app.api.schemas import SendIn, SendResultOut
from app.application.send_approved import SendApproved, SendRepos
from app.config import get_operator_settings, get_settings

router = APIRouter(tags=["send"])


class _UTCClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _send_repos(repos: Repos) -> SendRepos:
    return SendRepos(founder=repos.founder, outreach=repos.outreach,
                     message=repos.message, session=repos.session,
                     settings=getattr(repos, "settings", None))


def _load_oauth_client(path: str) -> tuple[str, str]:
    """`client_id` / `client_secret` from a Google Cloud OAuth client JSON
    - mirrors `app.cli._load_oauth_client`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    block = data.get("installed") or data.get("web") or {}
    client_id, client_secret = block.get("client_id"), block.get("client_secret")
    if not client_id or not client_secret:
        raise HTTPException(400, "gmail client secrets file is malformed")
    return client_id, client_secret


def _build_real_sender(repos: Repos, settings):
    """Only reachable when `repos.sender` was not injected - see the
    module docstring's safety note. Refuses (400, naming only the missing
    setting NAMES, never their values) before ever touching the network."""
    from app.adapters.db.repositories import GmailCredentialRepo
    from app.adapters.gmail import GmailSender

    missing = require_send_config(settings)
    if missing:
        raise HTTPException(400, {"error": "refusing to send: missing configuration", "missing": missing})
    client_id, client_secret = _load_oauth_client(settings.gmail_client_secrets_path)
    cred = GmailCredentialRepo(repos.session).get_by_account_email(settings.gmail_sender_address)
    if cred is None:
        raise HTTPException(400, f"no Gmail refresh token stored for {settings.gmail_sender_address} - "
                                 "run `gmail-auth` first")
    return GmailSender(client_id=client_id, client_secret=client_secret,
                       refresh_token=cred.refresh_token, sender_address=settings.gmail_sender_address)


@router.post("/send", response_model=SendResultOut)
def send(payload: SendIn, repos: Repos = Depends(get_repos)) -> SendResultOut:
    sender = repos.sender
    owns_sender = sender is None

    if owns_sender:
        settings = get_settings()
        sender = _build_real_sender(repos, settings)
        signature, attachment_path, cap = settings.signature, settings.resume_path, settings.daily_send_cap
    else:
        # Test/offline path (a `fake_sender`-injected `Repos`): the same
        # operator settings, minus the DB requirement.
        settings = get_operator_settings()
        signature, cap = settings.signature, settings.daily_send_cap
        attachment_path = settings.resume_path or None

    use_case = SendApproved(sender, _UTCClock(), _send_repos(repos),
                            signature=signature, attachment_path=attachment_path)
    try:
        result = use_case.run(cap=cap, limit=payload.limit)
    finally:
        if owns_sender:
            sender.close()

    return SendResultOut(
        sent=result.sent,
        skipped_cap=result.skipped_cap,
        skipped_not_reservable=result.skipped_not_reservable,
        needs_manual_check=result.needs_manual_check,
        errors=result.errors,
    )
