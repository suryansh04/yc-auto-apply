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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import Repos, get_repos, load_oauth_client, require_send_config
from app.api.schemas import SendIn, SendResultOut
from app.application.errors import OperatorError
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


def _build_real_sender(repos: Repos, settings):
    """Only reachable when `repos.sender` was not injected - see the
    module docstring's safety note. Refuses (400, naming only the missing
    setting NAMES, never their values) before ever touching the network."""
    from app.adapters.db.repositories import GmailCredentialRepo
    from app.adapters.gmail import GmailSender

    missing = require_send_config(settings)
    if missing:
        raise HTTPException(400, {"error": "refusing to send: missing configuration", "missing": missing})
    # Final fix wave (F7): the ONE loader, shared with `app.cli` and the
    # poll-replies job (`app.api.deps.load_oauth_client`); its
    # `RuntimeError` names what is wrong with the file, never its contents.
    try:
        client_id, client_secret = load_oauth_client(settings.gmail_client_secrets_path)
    except (RuntimeError, ValueError):
        raise HTTPException(400, "gmail client secrets file is malformed")
    cred = GmailCredentialRepo(repos.session).get_by_account_email(settings.gmail_sender_address)
    if cred is None:
        # F9: a fixed message - the account address is configuration
        # identity and never belongs in an HTTP body (T8's rule, as
        # `app.api.jobs._poll_replies` already follows).
        raise HTTPException(400, "no Gmail refresh token stored for the configured account - "
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
        result = use_case.run(cap=cap, limit=payload.limit,
                              only_outreach_id=payload.outreach_id)
    except OperatorError as exc:
        # A refusal written for the operator - currently "the resume file
        # cannot be read", raised before anything is reserved. 400, with
        # the sentence, because the fix is his and the screen is where he
        # is standing.
        raise HTTPException(400, str(exc)) from None
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
