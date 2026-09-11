"""Operator CLI for the pipeline (spec §9). Each command wires real adapters
and repos into an application-layer use case and runs it against the
configured database.

`domain/` and `application/` stay adapter-free (D8); this module is the one
place allowed to import both a concrete adapter and a concrete repo and glue
them together.
"""

from typing import Optional

import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings

app = typer.Typer(add_completion=False)


@app.callback()
def _callback() -> None:
    """YC Auto Apply operator CLI. Run a subcommand, e.g. `sync`."""
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
    """Resolve each NEEDS_EMAIL founder's email address (Task 12, spec §9,
    D18/D30). Published-email short circuit first, then candidate
    verification in priority order with early exit on the first `valid`.
    Stops the whole run (without failing anything) the moment the verifier
    signals its quota is exhausted — the free tier renews daily, so a
    paused run picks back up on the next invocation."""
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
        )
        verifier = MyEmailVerifierClient(settings.email_verifier_api_key)
        try:
            result = ResolveEmails(verifier, repos).run(limit=limit)
        finally:
            verifier.close()
        session.commit()

    typer.echo(
        f"resolved {result.resolved}, manual {result.manual}, "
        f"errors {result.errors}, "
        f"verifications used {result.verifications_used}, "
        f"paused on quota: {result.paused_on_quota}"
    )


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


def _gemini_client():
    from google import genai

    settings = get_settings()
    if not settings.gemini_api_key:
        raise typer.BadParameter("GEMINI_API_KEY is not set")
    return genai.Client(api_key=settings.gemini_api_key)


@app.command("parse-resume")
def parse_resume(path: str = typer.Argument(..., help="Resume file: .pdf, .txt or .md")) -> None:
    """Parse the resume into structured, citable claims (Task 13, spec §10).
    Re-runnable: replaces the single `resume_profile` row. Read every claim
    it prints — these are the only things the drafter may assert about you."""
    from app.adapters.db.repositories import ResumeProfileRepo
    from app.adapters.gemini import GeminiResumeParser
    from app.application.parse_resume import ParseResume

    settings = get_settings()
    raw_text = _read_resume_text(path)
    parser = GeminiResumeParser(_gemini_client(), model=settings.gemini_model)
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
    limit: Optional[int] = typer.Option(None, "--limit", help="Max founders to draft for this run.")
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
    from app.adapters.gemini import GeminiDrafter
    from app.application.draft_outreach import DraftOutreach, DraftRepos

    settings = get_settings()
    drafter = GeminiDrafter(_gemini_client(), model=settings.gemini_model)
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
        result = DraftOutreach(drafter, repos).run(limit=limit)
        session.commit()

    typer.echo(
        f"drafted {result.drafted}, manual write {result.manual_write}, "
        f"retried {result.retried}, errors {result.errors}"
    )


def _print_claims(claims: list[dict]) -> None:
    for i, c in enumerate(claims, 1):
        flag = "" if c.get("span_verified", True) else "  [span NOT found verbatim in resume]"
        typer.echo(f"{i:>3}. [{c.get('kind')}] {c.get('text')}{flag}")
        typer.echo(f"       span: {c.get('source_span')!r}")


if __name__ == "__main__":
    app()
