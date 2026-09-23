"""The resume PDF that `send` attaches to every letter.

Operator request (2026-09-22): read it and replace it from the browser.
It is the one part of an outgoing email nothing in the UI could show -
the letter was reviewable down to the byte while the attachment beside it
was a filename in a footer.

Three routes, all about the SAME file: the path in `RESUME_PATH`. No
request names a path, so there is nothing to traverse; `GET /api/resume`
serves exactly the file `send_approved` will attach, and `POST` replaces
it in place after keeping a timestamped copy of what was there.

Uploading a resume reads it: `POST /api/resume` replaces the file and
then does what `POST /api/resume/parse` does, because the claims the
drafter is held to (`resume_profile`) live in the database and a new PDF
sitting beside the old claims is a trap. Parsing used to be a separate
button, and before that `python -m app.cli parse-resume PATH` and
nothing else - which made a brand-new operator's first **Draft** a dead
end, since drafting fails until the resume has been read.

The parse route stays, for re-reading the same file after a failure.

RESUME_PATH may be blank: it then means `backend/resume/resume.pdf`
(`app.config.DEFAULT_RESUME_PATH`), so the Upload button works on a
fresh checkout without editing .env first.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.adapters.resume_text import read_resume_text
from app.api.deps import Repos, build_resume_parser, get_repos
from app.application.parse_resume import ParseResume
from app.config import get_operator_settings, get_settings

router = APIRouter(tags=["resume"])

# A resume is a PDF. Anything else is either a mistake or someone trying
# to make the API write an arbitrary file, and both answers are "no".
_PDF_MAGIC = b"%PDF-"
_MAX_BYTES = 10 * 1024 * 1024


class ResumeStatusOut(BaseModel):
    """Whether there is a resume to attach, and what the drafter knows.

    `claims` is the count from `resume_profile` - the parsed claims the
    validator holds a draft to. It is here because replacing the PDF does
    not reparse it, and an operator who swaps in a new resume should be
    told that the model is still working from the old one.
    """

    configured: bool
    filename: str | None = None
    size_bytes: int | None = None
    modified_at: str | None = None
    claims: int = 0


class ParseResultOut(BaseModel):
    """What a parse made of the resume.

    `claims` is what a letter may now assert about the operator.
    `unverified` are claims whose quoted span could not be found in the
    resume text verbatim - kept, but the drafter skips them unless asked
    otherwise. `dropped` never made it to the profile at all. Both counts
    are here because "it worked" and "it worked and threw half of it
    away" should not look the same on screen."""

    status: ResumeStatusOut
    claims: int
    unverified: int = 0
    dropped: int = 0


class ReplaceResultOut(ParseResultOut):
    """What an upload did: the new file, and what reading it produced.

    `parse_error` is set when the file was saved but could not be read -
    the upload itself succeeded, so this is a report, not a failure. The
    claims in `status` are then still the previous ones.
    """

    # 0 when the parse failed: the profile is untouched, and `status`
    # still reports what the drafter actually has.
    claims: int = 0
    parse_error: str | None = None


def _resume_path() -> Path:
    """Where the resume is. Never None: a blank RESUME_PATH means
    `backend/resume/resume.pdf` (`app.config.DEFAULT_RESUME_PATH`), so a
    first-time operator can upload one from the browser without editing
    a file first. The path is still a SETTING, never anything a request
    said."""
    return Path(get_operator_settings().resume_path)


@router.get("/resume/status", response_model=ResumeStatusOut)
def resume_status(repos: Repos = Depends(get_repos)) -> ResumeStatusOut:
    path = _resume_path()
    profile = repos.resume_profile.get_current()
    claims = len(getattr(profile, "claims", None) or []) if profile is not None else 0
    if not path.is_file():
        return ResumeStatusOut(configured=False, claims=claims)
    stat = path.stat()
    return ResumeStatusOut(
        configured=True,
        filename=path.name,
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        claims=claims,
    )


@router.get("/resume")
def get_resume() -> FileResponse:
    """The exact file `send_approved` attaches.

    `inline` so the browser opens it in a tab rather than downloading it -
    the point is to look at it before sending.

    Live finding (2026-09-23): the operator replaced the resume and kept
    seeing the old one. One URL always serves whatever file is at
    RESUME_PATH, so without a cache directive the browser is free to guess
    a freshness lifetime from Last-Modified and re-show the PDF it already
    had - and Chrome's PDF viewer does. `no-store` makes every look at
    this route a look at the current file.
    """
    path = _resume_path()
    if not path.is_file():
        raise HTTPException(404, "no resume file is configured")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{path.name}"',
            "Cache-Control": "no-store, must-revalidate",
        },
    )


@router.post("/resume", response_model=ReplaceResultOut)
async def replace_resume(
    file: UploadFile = File(...), repos: Repos = Depends(get_repos)
) -> ReplaceResultOut:
    """Replace the attached resume, keeping the old one beside it, and
    read the new file.

    The upload is checked before anything is written: a PDF header and a
    size bound. The previous file is renamed rather than deleted, because
    overwriting the only copy of a resume on a mis-click is not a failure
    this app should be capable of.

    Operator request (2026-09-23): the parse follows the upload. It used
    to be a separate button, on the argument that a model call should
    never be spent without being asked - but the claims are the only
    thing a letter may say about you, and a new PDF with the old claims
    beside it is a trap with no upside. The call is cheap and the
    intent is unambiguous: you uploaded a new resume because it is now
    the true one.

    The parse cannot undo the upload. The file is already replaced when
    it runs, so its failure is REPORTED (`parse_error`) rather than
    raised - a 500 here would mean "your resume is gone" to someone whose
    resume is in fact perfectly saved.
    """
    path = _resume_path()

    data = await file.read()
    if not data:
        raise HTTPException(400, "that file is empty")
    if len(data) > _MAX_BYTES:
        raise HTTPException(400, f"that file is larger than {_MAX_BYTES // (1024 * 1024)}MB")
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(400, "that is not a PDF")

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.stem}.{stamp}{path.suffix}"))
    path.write_bytes(data)

    try:
        parsed = parse_resume_route(repos)
    except HTTPException as exc:
        # `detail` is written for the operator by the parse route itself
        # and carries a type name at worst, never a provider response.
        return ReplaceResultOut(status=resume_status(repos), parse_error=str(exc.detail))
    return ReplaceResultOut(
        status=parsed.status,
        claims=parsed.claims,
        unverified=parsed.unverified,
        dropped=parsed.dropped,
    )


@router.post("/resume/parse", response_model=ParseResultOut)
def parse_resume_route(repos: Repos = Depends(get_repos)) -> ParseResultOut:
    """Read the resume and replace the claims a letter may assert.

    The same work `python -m app.cli parse-resume` does, on the file
    already configured - no path crosses this boundary, here either.

    Only the operator's OWN resume is sent to the model, never a
    founder's data, which is why this is allowed on a free-tier Gemini
    key where `draft` refuses outright (ruling R4). Re-runnable: it
    replaces the single `resume_profile` row, and `ParseResume` refuses
    to replace it with nothing.
    """
    path = _resume_path()
    if not path.is_file():
        raise HTTPException(404, "there is no resume file at RESUME_PATH")

    parser = getattr(repos, "parser", None)
    if parser is None:
        try:
            parser = build_resume_parser(get_settings())
        except RuntimeError as exc:
            # A missing SETTING name, never its value.
            raise HTTPException(400, str(exc)) from None

    try:
        text = read_resume_text(str(path))
    except Exception as exc:  # noqa: BLE001 - a broken PDF is the operator's to fix
        raise HTTPException(400, f"could not read that file ({type(exc).__name__})") from None

    use_case = ParseResume(parser, repos.resume_profile)
    try:
        claims = use_case.run(text)
    except ValueError as exc:
        # `ParseResume`'s own refusals are written for a human: an empty
        # resume, or a parse that produced nothing usable. The existing
        # profile is left exactly as it was in both cases.
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - a provider failure must not leak its body
        raise HTTPException(502, f"the resume parser failed ({type(exc).__name__})") from None

    repos.session.commit()
    return ParseResultOut(
        status=resume_status(repos),
        claims=len(claims),
        unverified=len(use_case.unverified),
        dropped=len(use_case.dropped),
    )
