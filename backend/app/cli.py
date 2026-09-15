"""Operator CLI for the pipeline (spec §9). Each command wires real adapters
and repos into an application-layer use case and runs it against the
configured database.

`domain/` and `application/` stay adapter-free (D8); this module is the one
place allowed to import both a concrete adapter and a concrete repo and glue
them together.
"""

import os
import subprocess
import sys
import tempfile
from typing import Optional

import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings

app = typer.Typer(add_completion=False)


@app.callback()
def _callback() -> None:
    """YC Auto Apply operator CLI. Run a subcommand, e.g. `sync`."""
    # Live-run fix (2026-09-13): a Windows console defaults to cp1252 and
    # crashes `typer.echo` on any character outside it (a resume's "~",
    # a founder's name, a news headline). Emit UTF-8 regardless; the
    # `errors="replace"` fallback guarantees output can never crash a run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # Present even though empty: without a callback, Typer collapses a
    # single-command app so its one command runs with no subcommand name at
    # all, breaking the documented invocation `python -m app.cli sync`. A
    # (no-op) callback keeps subcommand dispatch active regardless of how
    # many commands are registered.


def _session() -> Session:
    engine = create_engine(get_settings().database_url)
    return Session(bind=engine)


@app.command()
def sync() -> None:
    """Sync the target company list from the yc-oss dataset (Task 10)."""
    from app.adapters.db.repositories import CompanyRepo
    from app.adapters.yc_oss import YCOSSDirectory
    from app.application.sync_companies import SyncCompanies

    with _session() as session:
        result = SyncCompanies(YCOSSDirectory(), CompanyRepo(session)).run()
        session.commit()
    typer.echo(
        f"fetched {result.fetched}, matched {result.matched}, upserted {result.upserted}"
    )


@app.command()
def scrape() -> None:
    """Scrape founders, news, launches and job postings for every synced
    target company (Task 11). Rate-limited to 1 request/second inside
    `YCPageSource`; a company whose page fails to fetch or parse is skipped
    with its error recorded, and the run continues with the rest."""
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyNewsRepo,
        CompanyRepo,
        FounderRepo,
        JobPostingRepo,
        OutreachRepo,
    )
    from app.adapters.yc_page import YCPageSource
    from app.application.scrape_company_page import ScrapeCompanyPage, ScrapeRepos

    with _session() as session:
        companies = CompanyRepo(session).list_by_status(status="Active")
        repos = ScrapeRepos(
            founder=FounderRepo(session),
            job_posting=JobPostingRepo(session),
            news=CompanyNewsRepo(session),
            launch=CompanyLaunchRepo(session),
            outreach=OutreachRepo(session),
            session=session,
        )
        use_case = ScrapeCompanyPage(YCPageSource(), repos)

        total_founders = total_new_founders = errors = 0
        for company in companies:
            result = use_case.run(company)
            session.commit()
            total_founders += result.founders
            total_new_founders += result.new_founders
            if result.error:
                errors += 1
                typer.echo(f"  error: {result.error}", err=True)

    typer.echo(
        f"companies {len(companies)}, founders {total_founders} "
        f"(new {total_new_founders}), errors {errors}"
    )


@app.command()
def resolve(
    limit: Optional[int] = typer.Option(None, "--limit", help="Max founders to resolve this run.")
) -> None:
    """Resolve each NEW / NEEDS_EMAIL founder's email address (Task 12, spec
    §9, D18/D30; NEW rows from `scrape` are stepped to NEEDS_EMAIL first -
    Task 16). Published-email short circuit first, then candidate
    verification in priority order with early exit on the first `valid`.
    Stops the whole run (without failing anything) the moment the verifier
    signals its quota is exhausted — the free tier renews daily, so a
    paused run picks back up on the next invocation. Commits after every
    founder (ruling FR-3), so interrupting a run keeps every verdict
    already paid for."""
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyRepo,
        EmailCandidateRepo,
        FounderRepo,
        OutreachRepo,
    )
    from app.adapters.email_verifier import MyEmailVerifierClient
    from app.application.resolve_emails import ResolveEmails, ResolveRepos

    settings = get_settings()
    with _session() as session:
        repos = ResolveRepos(
            founder=FounderRepo(session),
            company=CompanyRepo(session),
            launch=CompanyLaunchRepo(session),
            outreach=OutreachRepo(session),
            email_candidate=EmailCandidateRepo(session),
            session=session,
        )
        verifier = MyEmailVerifierClient(settings.email_verifier_api_key)
        try:
            # Final fix wave (F4, ruling FR-3): the use case commits after
            # every founder, so a Ctrl-C mid-run keeps every verdict already
            # paid for out of the 100/day quota.
            result = ResolveEmails(verifier, repos).run(limit=limit)
        finally:
            verifier.close()

    typer.echo(
        f"resolved {result.resolved}, manual {result.manual}, "
        f"errors {result.errors}, "
        f"verifications used {result.verifications_used}, "
        f"paused on quota: {result.paused_on_quota}"
    )


# --- Final fix wave (F3, ruling FR-1): the manual-email path --------------------


def _manual_repos(session):
    from app.adapters.db.repositories import (
        CompanyRepo,
        EmailCandidateRepo,
        FounderRepo,
        OutreachRepo,
    )
    from app.application.resolve_manual_email import ManualEmailRepos

    return ManualEmailRepos(
        founder=FounderRepo(session),
        company=CompanyRepo(session),
        outreach=OutreachRepo(session),
        email_candidate=EmailCandidateRepo(session),
    )


def render_manual_table(items) -> str:
    """Pure: one block per `NEEDS_MANUAL_EMAIL` founder - the outreach id
    to pass to `set-email`, founder, company, domain, LinkedIn, then the
    candidates already tried with their verdicts and, when `resolve`
    persisted one, the launch address it could not attribute (hint)."""
    if not items:
        return "no founders need a manual email\n"
    header = f"{'ID':>5}  {'FOUNDER':<24} {'COMPANY':<20} {'DOMAIN':<24} LINKEDIN"
    lines = [header]
    for item in items:
        company = item.company
        lines.append(
            f"{item.outreach.id:>5}  "
            f"{(getattr(item.founder, 'full_name', None) or '')[:24]:<24} "
            f"{(getattr(company, 'name', None) or '')[:20]:<20} "
            f"{(getattr(company, 'domain', None) or '-')[:24]:<24} "
            f"{getattr(item.founder, 'linkedin_url', None) or '-'}"
        )
        if item.candidates:
            tried = ", ".join(f"{c.address}={c.verdict or '?'}" for c in item.candidates)
        else:
            tried = "(none - no domain)"
        lines.append(f"       candidates: {tried}")
        if item.published_hint:
            lines.append(f"       published hint: {item.published_hint} "
                         "(from a launch post; could not be attributed to this founder)")
    lines.append(f"{len(items)} founder(s); resolve one with `set-email ID ADDRESS`, "
                 "or `set-email ID --unresolved` to give up")
    return "\n".join(lines) + "\n"


@app.command("list-manual")
def list_manual() -> None:
    """List every NEEDS_MANUAL_EMAIL founder (spec §9: all candidates
    invalid, a catch-all domain, no domain, or an unattributable published
    email) with the candidates already tried, the LinkedIn URL and any
    published-email hint - what a human works from to pick or find the
    address, then `set-email ID ADDRESS`."""
    from app.application.resolve_manual_email import ResolveManualEmail

    with _session() as session:
        items = ResolveManualEmail(_manual_repos(session)).pending()
        typer.echo(render_manual_table(items), nl=False)


@app.command("set-email")
def set_email(
    outreach_id: int = typer.Argument(..., help="The ID column from `list-manual`."),
    address: Optional[str] = typer.Argument(None, help="The founder's email address."),
    unresolved: bool = typer.Option(
        False, "--unresolved",
        help="Give up on this founder instead: NEEDS_MANUAL_EMAIL -> UNRESOLVED (terminal).",
    ),
) -> None:
    """Resolve one NEEDS_MANUAL_EMAIL founder by hand: store ADDRESS and
    move the outreach to READY_TO_DRAFT (the next `draft` run picks it
    up), or with --unresolved mark it UNRESOLVED. Refuses any outreach
    that is not in NEEDS_MANUAL_EMAIL and any ADDRESS that does not look
    like local@domain.tld."""
    from app.application.resolve_manual_email import ResolveManualEmail
    from app.domain.states import IllegalTransition

    if unresolved == (address is not None):
        typer.echo("give exactly one of ADDRESS or --unresolved", err=True)
        raise typer.Exit(code=2)
    with _session() as session:
        use_case = ResolveManualEmail(_manual_repos(session))
        try:
            if unresolved:
                use_case.give_up(outreach_id)
                session.commit()
                typer.echo(f"outreach {outreach_id} marked unresolved")
                return
            stored = use_case.set_email(outreach_id, address)
        except (ValueError, IllegalTransition) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        session.commit()
    typer.echo(f"outreach {outreach_id}: resolved_email set to {stored}, now ready_to_draft")


def _read_resume_text(path: str) -> str:
    """PDF via `pypdf` (text extraction only — no OCR; a scanned-image resume
    yields empty text and is rejected downstream), or a plain `.txt`/`.md`
    read for anything else."""
    from pathlib import Path

    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return p.read_text(encoding="utf-8")


_FREE_TIER_NOTICE = (
    "GEMINI_TIER is 'free': Google may use free-tier inputs and outputs for "
    "training (spec D12)."
)


def _gemini_client():
    from google import genai

    settings = get_settings()
    if not settings.gemini_api_key:
        raise typer.BadParameter("GEMINI_API_KEY is not set")
    return genai.Client(api_key=settings.gemini_api_key)


def _resume_parser():
    """Provider switch: the configured LLM's `ResumeParser` adapter."""
    settings = get_settings()
    if settings.llm_provider == "sarvam":
        from app.adapters import sarvam

        if not settings.sarvam_api_key:
            raise typer.BadParameter("SARVAM_API_KEY is not set")
        return sarvam.SarvamResumeParser(settings.sarvam_api_key, model=settings.sarvam_model)
    from app.adapters.gemini import GeminiResumeParser

    return GeminiResumeParser(_gemini_client(), model=settings.gemini_model)


def _drafter():
    """Provider switch: the configured LLM's `Drafter` adapter. Shared
    with the UI job runner via `app.api.deps.build_drafter` (fix round 2,
    ruling T6-6) - one rule, not two that can drift on a new provider or a
    changed model default. `RuntimeError` (that shared function's only
    failure mode, since it must stay usable with no CLI dependency) is
    translated here into `typer.BadParameter` for the CLI's own nice error
    formatting - unchanged CLI behaviour from before the extraction."""
    from app.api.deps import build_drafter

    try:
        return build_drafter(get_settings())
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _critic():
    """Provider switch for the advisory `Critic` (D22, Task 13). Shared
    with the UI job runner via `app.api.deps.build_critic`, the same
    extraction `_drafter`/`build_drafter` above already did (T6-6/T9). Same
    error translation as `_drafter` - but note the critic is advisory only,
    so `draft`'s own call site never lets a `typer.BadParameter` from here
    abort a run; see its call site below."""
    from app.api.deps import build_critic

    try:
        return build_critic(get_settings())
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _on_gemini_free_tier() -> bool:
    # Task 6 fix round 1 (ruling T6-3): shared with `app.api.jobs`'s UI
    # job runner via `app.api.deps.on_gemini_free_tier`, the same
    # extraction Task 5 did for the send guards - one rule, not two that
    # can drift.
    from app.api.deps import on_gemini_free_tier

    return on_gemini_free_tier(get_settings())


def _refuse_free_tier_for_founder_data(allow_free_tier: bool) -> None:
    """Fix round 1 (F2, ruling R4): `draft` sends founders' names, titles
    and bios to Gemini. On a free-tier key that is third-party personal
    data going to a provider that may train on it (D12, spec §14 "fails
    loudly"), so the run refuses unless the operator opts in explicitly —
    the intended use being the brief's own 'draft five and read them'
    check (14.6), never a full run."""
    if _on_gemini_free_tier() and not allow_free_tier:
        typer.echo(
            f"refusing to draft: {_FREE_TIER_NOTICE} Founder data would be sent to a "
            "free-tier key. Set GEMINI_TIER=paid, or pass --allow-free-tier for a "
            "small deliberate test run.",
            err=True,
        )
        raise typer.Exit(code=2)


@app.command("parse-resume")
def parse_resume(path: str = typer.Argument(..., help="Resume file: .pdf, .txt or .md")) -> None:
    """Parse the resume into structured, citable claims (Task 13, spec §10).
    Re-runnable: replaces the single `resume_profile` row. Read every claim
    it prints — these are the only things the drafter may assert about you."""
    from app.adapters.db.repositories import ResumeProfileRepo
    from app.application.parse_resume import ParseResume

    settings = get_settings()
    raw_text = _read_resume_text(path)
    if _on_gemini_free_tier():
        # Only the user's own resume goes out here (no third-party data), so
        # a warning is enough (ruling R4); `draft` is the hard refusal.
        typer.echo(f"warning: {_FREE_TIER_NOTICE} Your resume text is being sent.", err=True)
    parser = _resume_parser()
    with _session() as session:
        use_case = ParseResume(parser, ResumeProfileRepo(session))
        claims = use_case.run(raw_text)
        session.commit()

    _print_claims([c.__dict__ for c in claims])
    if use_case.dropped:
        typer.echo(f"dropped {len(use_case.dropped)} claim(s) with an unknown kind or empty text:", err=True)
        for c in use_case.dropped:
            typer.echo(f"  {c}", err=True)
    typer.echo(f"parsed {len(claims)} claim(s), {len(use_case.unverified)} with an unverifiable span")


@app.command("show-claims")
def show_claims() -> None:
    """Print the claims currently persisted in `resume_profile` (Task 13)."""
    from app.adapters.db.repositories import ResumeProfileRepo

    with _session() as session:
        profile = ResumeProfileRepo(session).get_current()
        if profile is None:
            typer.echo("no resume profile yet - run `parse-resume PATH` first", err=True)
            raise typer.Exit(code=1)
        claims = list(profile.claims or [])
    _print_claims(claims)
    typer.echo(f"{len(claims)} claim(s)")


@app.command()
def draft(
    limit: Optional[int] = typer.Option(None, "--limit", help="Max founders to draft for this run."),
    keep_unverified: bool = typer.Option(
        False, "--keep-unverified",
        help="Also offer claims whose source_span was not found in the resume (read them with show-claims first).",
    ),
    allow_free_tier: bool = typer.Option(
        False, "--allow-free-tier",
        help="Permit sending founder data to a free-tier Gemini key (small test runs only).",
    ),
) -> None:
    """Draft an internship enquiry for each READY_TO_DRAFT founder (Task 14,
    spec §10). Sources are offered in priority order news > launch >
    intern-appropriate posting > long description (D17/D23); each draft is
    run through the deterministic validator, retried once on failure, and
    a second failure is surfaced in the approval queue flagged for a manual
    write - nothing is silently dropped. A drafter exception on one founder
    is absorbed inside the use case (that founder stays READY_TO_DRAFT), so
    the single commit at the end holds everything drafted this run; use
    `--limit` to keep any one run's exposure small."""
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyNewsRepo,
        CompanyRepo,
        FounderRepo,
        JobPostingRepo,
        MessageRepo,
        OutreachRepo,
        ResumeProfileRepo,
    )
    from app.application.draft_outreach import DraftOutreach, DraftRepos

    settings = get_settings()
    _refuse_free_tier_for_founder_data(allow_free_tier)
    drafter = _drafter()
    critic = _critic()  # Task 13 (D22): advisory only - shares the drafter's provider/key
    with _session() as session:
        repos = DraftRepos(
            founder=FounderRepo(session),
            company=CompanyRepo(session),
            news=CompanyNewsRepo(session),
            launch=CompanyLaunchRepo(session),
            job_posting=JobPostingRepo(session),
            outreach=OutreachRepo(session),
            message=MessageRepo(session),
            resume_profile=ResumeProfileRepo(session),
        )
        result = DraftOutreach(
            drafter, repos, keep_unverified=keep_unverified, critic=critic,
        ).run(limit=limit)
        session.commit()

    typer.echo(
        f"drafted {result.drafted}, manual write {result.manual_write}, "
        f"retried {result.retried}, errors {result.errors}"
        + (" - ABORTED after consecutive drafter failures (see outreach.last_error)"
           if result.aborted else "")
    )
    if result.aborted:
        raise typer.Exit(code=1)


def _print_claims(claims: list[dict]) -> None:
    for i, c in enumerate(claims, 1):
        flag = "" if c.get("span_verified", True) else "  [span NOT found verbatim in resume]"
        typer.echo(f"{i:>3}. [{c.get('kind')}] {c.get('text')}{flag}")
        typer.echo(f"       span: {c.get('source_span')!r}")


# --- Task 15: Gmail sending ----------------------------------------------------

_MIN_PAUSE_SECONDS, _MAX_PAUSE_SECONDS = 20.0, 75.0  # spec §18: randomised inter-send delay


class _UTCClock:
    def now(self):
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)


def _random_pause() -> None:
    import random
    import time

    time.sleep(random.uniform(_MIN_PAUSE_SECONDS, _MAX_PAUSE_SECONDS))


_MAX_TO_SELF = 3  # fix round 1 (F8): `--to-self` copies never count toward the DB cap, but do toward Gmail's


def _refuse(action: str, missing: list[str]) -> None:
    if missing:
        typer.echo(f"refusing to {action}: missing configuration", err=True)
        for m in missing:
            typer.echo(f"  - {m}", err=True)
        raise typer.Exit(code=2)


def _require_gmail_config(settings) -> None:
    """What `gmail-auth` needs (fix round 1, F5): only the sending address
    and the OAuth client-secrets file — authorising Gmail must not wait on
    the resume or the signature being in place. Shared with
    `app.api.deps.require_gmail_config` so this rule lives in one place."""
    from app.api.deps import require_gmail_config

    _refuse("authorise gmail", require_gmail_config(settings))


def _require_send_config(settings) -> None:
    """Refuse to build a sender until every send-side setting is present
    (all default to empty in `app.config`, so a fresh `.env` can never send
    by accident): the Gmail settings plus a real, non-blank signature
    (spec §18) and an existing resume PDF. Shared with
    `app.api.deps.require_send_config` (Task 5) so the CLI and the browser
    send route cannot drift on what "configured enough to send" means."""
    from app.api.deps import require_send_config

    _refuse("send", require_send_config(settings))


def _load_oauth_client(path: str) -> tuple[str, str]:
    """`client_id` / `client_secret` from a Google Cloud OAuth client JSON
    (`{"installed": {...}}` for a Desktop app, or `{"web": {...}}`)."""
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    block = data.get("installed") or data.get("web") or {}
    client_id, client_secret = block.get("client_id"), block.get("client_secret")
    if not client_id or not client_secret:
        raise typer.BadParameter("client secrets JSON has no installed/web client_id + client_secret")
    return client_id, client_secret


def _gmail_sender(client_id: str, client_secret: str, refresh_token: str, sender_address: str):
    from app.adapters.gmail import GmailSender

    return GmailSender(client_id=client_id, client_secret=client_secret,
                       refresh_token=refresh_token, sender_address=sender_address)


# Task 7 (Phase 2, spec §5): `gmail-auth` now requests BOTH the send scope
# (Task 15) and read-only (needed by `app.adapters.gmail_reader.GmailReader`
# to poll replies). A refresh token minted under the old send-only scope
# cannot read mail - Google enforces scopes on the token, not just on the
# consent screen - so an operator who authorised before this change must
# re-run `gmail-auth` once; the command prints a note about this below.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


@app.command("gmail-auth")
def gmail_auth() -> None:
    """One-time OAuth bootstrap (Task 15, spec §14; widened in Task 7,
    spec §5): opens a browser for the Google consent screen with the
    `gmail.send` and `gmail.readonly` scopes, then stores the resulting
    REFRESH token in the `gmail_credentials` table (never in a file) under
    GMAIL_SENDER_ADDRESS. Re-run it if `send` reports `invalid_grant`
    (token revoked/expired), or once after upgrading past Task 7 if the
    stored token predates the read scope (a send-only token cannot read
    mail; Google enforces scopes on the token itself). Not covered by the
    offline suite: it needs a browser and Google's servers."""
    from app.adapters.db.repositories import GmailCredentialRepo

    settings = get_settings()
    _require_gmail_config(settings)
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(settings.gmail_client_secrets_path, scopes=GMAIL_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        typer.echo("Google returned no refresh token - revoke the app's access at "
                   "https://myaccount.google.com/permissions and run gmail-auth again.", err=True)
        raise typer.Exit(code=1)
    with _session() as session:
        GmailCredentialRepo(session).upsert(
            account_email=settings.gmail_sender_address,
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri or "https://oauth2.googleapis.com/token",
            scopes=list(creds.scopes or GMAIL_SCOPES),
        )
        session.commit()
    typer.echo(f"stored a refresh token for {settings.gmail_sender_address} (scopes: gmail.send, gmail.readonly)")
    typer.echo("NOTE: if you authorised this account before this change, that older token was "
               "send-only and cannot read replies - this run replaced it with one covering both "
               "scopes, so no further action is needed. If a colleague's token is still send-only, "
               "they must run `gmail-auth` again once.")


@app.command()
def send(
    limit: Optional[int] = typer.Option(None, "--limit", help="Max emails to send this run (on top of the daily cap)."),
    to_self: bool = typer.Option(
        False, "--to-self",
        help="Smoke test: send the first approved draft(s) to GMAIL_SENDER_ADDRESS instead of the founders. "
             f"Reserves nothing and changes no state, so the real send can follow. --limit is clamped to {_MAX_TO_SELF}.",
    ),
) -> None:
    """Send every APPROVED outreach through Gmail (Task 15, spec §13/§18),
    at most DAILY_SEND_CAP per rolling 24-hour window (ruling R-A), counting
    everything already sent in the 24 hours before this run.
    Reserve-before-send: each message row is claimed by a committed
    conditional UPDATE before Gmail is called, so a crash can never produce
    a second copy - a row left reserved without a Gmail id is reported as
    `needs manual check` on the next run and is never re-sent. The first
    Gmail failure stops the run (auth/quota errors should pause, not burn
    through the queue); fix the cause and re-run."""
    from app.adapters.db.repositories import (
        FounderRepo,
        GmailCredentialRepo,
        MessageRepo,
        OutreachRepo,
        SettingsRepo,
    )
    from app.application.send_approved import SendApproved, SendRepos

    settings = get_settings()
    _require_send_config(settings)
    client_id, client_secret = _load_oauth_client(settings.gmail_client_secrets_path)
    with _session() as session:
        cred = GmailCredentialRepo(session).get_by_account_email(settings.gmail_sender_address)
        if cred is None:
            typer.echo(f"no Gmail refresh token stored for {settings.gmail_sender_address} - "
                       "run `gmail-auth` first", err=True)
            raise typer.Exit(code=2)
        sender = _gmail_sender(client_id, client_secret, cred.refresh_token, settings.gmail_sender_address)
        repos = SendRepos(
            founder=FounderRepo(session),
            outreach=OutreachRepo(session),
            message=MessageRepo(session),
            session=session,
            settings=SettingsRepo(session),
        )
        use_case = SendApproved(
            sender, _UTCClock(), repos,
            signature=settings.signature,
            attachment_path=settings.resume_path,
            pause_between_sends=_random_pause,
        )
        try:
            if to_self:
                n = use_case.send_to_self(settings.gmail_sender_address,
                                          limit=min(limit or 1, _MAX_TO_SELF))
                typer.echo(f"sent {n} test copy/copies to {settings.gmail_sender_address}; "
                           "nothing was reserved or marked sent")
                return
            result = use_case.run(cap=settings.daily_send_cap, limit=limit)
        finally:
            sender.close()

    typer.echo(
        f"sent {result.sent}, skipped (daily cap) {result.skipped_cap}, "
        f"not reservable {result.skipped_not_reservable}, "
        f"needs manual check {result.needs_manual_check}, errors {result.errors}"
    )
    if result.needs_manual_check:
        typer.echo(
            "needs manual check: a message row has sent_at set but no gmail_message_id "
            "(a previous run crashed between reserving and recording). Look in the Gmail "
            "Sent folder; if it went out, set gmail_message_id/gmail_thread_id and the "
            "outreach state to 'sent' by hand, otherwise clear sent_at to let it send.",
            err=True,
        )


# --- Task 16: terminal review loop --------------------------------------------

_SOURCE_PREVIEW_CHARS = 300
_KEY_LEGEND = "[a]pprove [e]dit [d]raft again [r]eject [s]kip [q]uit"
_RULE = "-" * 72


def render_review_screen(
    message, source, *, company=None, founder=None, signature: str = "", wire_text: str | None = None,
    index: int | None = None, total: int | None = None, to: str | None = None,
    last_error: str | None = None,
) -> str:
    """Pure: one draft as the reviewer sees it (spec §16 review queue, D5).

    Shows the company and founder, the CITED SOURCE beside the draft so a
    claim can be checked at a glance instead of by re-researching the
    company, and - ruling R2 - the composed wire text (`compose_wire_text`:
    greeting + body + signature + opt-out), never the bare stored body, so
    what the human approves is byte-for-byte what `send` will put on the
    wire. `wire_text` may be passed pre-composed; otherwise it is composed
    here from `message.body`, `founder.full_name` and `signature`.
    `message` may be `None` for a row flagged needs-manual-write that was
    never drafted; `source` accepts either a resolved `SourceRow` (`kind`,
    `text`) or a raw research row (`title`/`body`)."""
    from app.domain.compose import compose_wire_text, first_name

    counter = f"Draft {index} of {total}" if index is not None and total is not None else "Draft"
    lines = [f"==== {counter} ===="]

    if company is not None:
        one_liner = getattr(company, "one_liner", None)
        lines.append(f"Company: {getattr(company, 'name', '') or ''}"
                     + (f" - {one_liner}" if one_liner else ""))
    if founder is not None:
        title = getattr(founder, "title", None)
        lines.append(f"Founder: {getattr(founder, 'full_name', '') or ''}"
                     + (f" ({title})" if title else ""))
    if to:
        lines.append(f"To: {to}")

    kind = (getattr(source, "kind", None)
            or (getattr(message, "research_source_type", None) if message is not None else None))
    text = _source_text(source)
    if source is None or not text:
        lines.append("SOURCE: (none cited)")
    else:
        if len(text) > _SOURCE_PREVIEW_CHARS:
            text = text[:_SOURCE_PREVIEW_CHARS] + "..."
        lines.append(f"SOURCE [{kind or '?'}]: {text}")

    lines.append(_RULE)
    if message is None:
        lines.append("(no draft yet - press [e] to write one)")
        body = ""
    else:
        body = getattr(message, "body", "") or ""
        lines.append(f"Subject: {getattr(message, 'subject', '') or ''}")
        lines.append("--- as it will be sent ---")
        if wire_text is None:
            greeting_name = first_name(getattr(founder, "full_name", None) if founder is not None else None)
            wire_text = compose_wire_text(greeting_name, body, signature)
        lines.append(wire_text)
    lines.append(_RULE)
    lines.append(f"{len(body.split())} words in the body")
    if last_error:
        lines.append(f"NEEDS MANUAL WRITE: {last_error}")
    lines.append(_KEY_LEGEND)
    return "\n".join(lines) + "\n"


def _source_text(source) -> str:
    if source is None:
        return ""
    for attr in ("title", "text", "body"):
        value = getattr(source, attr, None)
        if value:
            return " ".join(str(value).split())
    return ""


def _editor_text(subject: str | None, body: str | None) -> str:
    return f"{subject or ''}\n\n{body or ''}"


def _split_edit(text: str) -> tuple[str, str]:
    """Inverse of `_editor_text`: first line is the subject, the rest (after
    an optional blank line) is the body; both stripped."""
    lines = text.splitlines()
    if not lines:
        return "", ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()


def _read_key() -> str:
    """One keypress, no Enter needed. Monkeypatched in tests."""
    return typer.getchar()


def _edit_text(text: str) -> str | None:
    """Open `$EDITOR` (or `$VISUAL`, else notepad/vi) on `text` and return
    what was saved; `None` if the editor failed or nothing changed.

    Live finding (2026-09-13): this used `click.edit`, but click is not a
    declared dependency and typer 0.27 no longer installs it, so `review`
    died with ModuleNotFoundError on a clean venv. Self-contained now.
    Monkeypatched in tests.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        editor = "notepad" if sys.platform == "win32" else "vi"

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="yc-draft-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        before = os.path.getmtime(path)
        # shell=True so an EDITOR carrying its own arguments ("code -w",
        # a quoted interpreter path) works the way a shell would run it.
        if subprocess.call(f'{editor} "{path}"', shell=True) != 0:
            return None
        if os.path.getmtime(path) == before:
            return None
        with open(path, encoding="utf-8") as fh:
            edited = fh.read()
        return None if edited == text else edited
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _review_repos(session):
    from app.adapters.db.repositories import (
        CompanyLaunchRepo,
        CompanyNewsRepo,
        CompanyRepo,
        FounderRepo,
        JobPostingRepo,
        MessageRepo,
        OutreachRepo,
        ResumeProfileRepo,
    )
    from app.application.review_outreach import ReviewRepos

    return ReviewRepos(
        founder=FounderRepo(session),
        company=CompanyRepo(session),
        outreach=OutreachRepo(session),
        message=MessageRepo(session),
        news=CompanyNewsRepo(session),
        launch=CompanyLaunchRepo(session),
        job_posting=JobPostingRepo(session),
        resume_profile=ResumeProfileRepo(session),
    )


def _review_loop(use_case, *, signature: str, clock, dry_run: bool, commit) -> dict[str, int]:
    """The interactive loop over `use_case.pending()`. Every action goes
    through the use case and is committed right after it succeeds; skip,
    quit, a refused approve, an aborted editor and a failed edit commit
    nothing. `--dry-run` renders every row and never reads a key."""
    from app.domain.states import IllegalTransition

    counts = {"approved": 0, "edited": 0, "redrafted": 0, "rejected": 0, "skipped": 0}
    items = use_case.pending()
    if not items:
        typer.echo("nothing pending approval")
        return counts
    total = len(items)

    for index, item in enumerate(items, 1):
        pending_text: str | None = None  # a rejected edit, re-offered in the editor
        while True:
            typer.echo(render_review_screen(
                item.message, item.source, company=item.company, founder=item.founder,
                signature=signature, index=index, total=total,
                to=getattr(item.outreach, "resolved_email", None),
                last_error=getattr(item.outreach, "last_error", None),
            ))
            if dry_run:
                break
            key = _read_key().lower()
            try:
                if key == "a":
                    use_case.approve(item.outreach.id, clock.now())
                    commit()
                    counts["approved"] += 1
                    typer.echo("approved")
                    break
                if key == "d":
                    # Fix round 1 (T16-1): send it back for a fresh draft -
                    # the only repair for a row flagged for its citation.
                    use_case.redraft(item.outreach.id)
                    commit()
                    counts["redrafted"] += 1
                    typer.echo("sent back to READY_TO_DRAFT; the next `draft` run replaces this draft")
                    break
                if key == "r":
                    use_case.reject(item.outreach.id)
                    commit()
                    counts["rejected"] += 1
                    typer.echo("rejected (discarded)")
                    break
                if key == "s":
                    use_case.skip(item.outreach.id)
                    counts["skipped"] += 1
                    break
                if key == "q":
                    typer.echo(_summary(counts))
                    return counts
                if key == "e":
                    if pending_text is None:
                        pending_text = _editor_text(
                            getattr(item.message, "subject", None) if item.message is not None else None,
                            getattr(item.message, "body", None) if item.message is not None else None,
                        )
                    edited = _edit_text(pending_text)
                    if edited is None:
                        typer.echo("edit aborted; nothing changed")
                        continue
                    subject, body = _split_edit(edited)
                    pending_text = edited   # F8: re-offered whether edit returns errors or raises
                    errors = use_case.edit(item.outreach.id, subject, body)
                    if errors:
                        typer.echo("edit rejected by the validator; nothing saved:", err=True)
                        for e in errors:
                            typer.echo(f"  - {e}", err=True)
                        continue
                    commit()
                    counts["edited"] += 1
                    pending_text = None
                    item = use_case.load(item.outreach.id)
                    typer.echo("saved; preview re-composed from the edited body")
                    continue
                typer.echo(f"unknown key {key!r}: {_KEY_LEGEND}", err=True)
            except (ValueError, IllegalTransition) as exc:
                typer.echo(str(exc), err=True)
                continue

    typer.echo(_summary(counts))
    return counts


def _summary(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items())


@app.command()
def review(
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Render every pending draft and exit without prompting."),
    keep_unverified: bool = typer.Option(
        False, "--keep-unverified",
        help="Also offer claims whose source_span was not found in the resume (read them with "
             "show-claims first). Pass it if the drafts were made with `draft --keep-unverified`, "
             "so an edit is validated against the same claims.",
    ),
) -> None:
    """Review each PENDING_APPROVAL draft in the terminal (Task 16, spec D5):
    the company, the founder, the cited research source and the message as
    it will be sent (greeting, body, signature, opt-out - ruling R2).
    [a]pprove stamps approved_at and makes the draft eligible for `send`;
    [e]dit opens $EDITOR on the subject and body, re-runs the validator and
    keeps nothing that fails it; [d]raft again sends the row back to
    READY_TO_DRAFT for the next `draft` run (the fix for a draft flagged
    for its citation); [r]eject discards the founder; [s]kip leaves the row
    for next time; [q]uit stops. Each action is committed on its own, so
    quitting mid-way loses nothing already decided."""
    settings = get_settings()
    from app.application.review_outreach import ReviewOutreach

    if not settings.signature.strip():
        typer.echo("warning: SIGNATURE is not set - the preview below omits it, and `send` "
                   "will refuse until it is configured", err=True)
    with _session() as session:
        use_case = ReviewOutreach(_review_repos(session), keep_unverified=keep_unverified)
        _review_loop(use_case, signature=settings.signature, clock=_UTCClock(),
                     dry_run=dry_run, commit=session.commit)


if __name__ == "__main__":
    app()
