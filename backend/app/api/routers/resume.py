"""The resume PDF that `send` attaches to every letter.

Operator request (2026-09-22): read it and replace it from the browser.
It is the one part of an outgoing email nothing in the UI could show -
the letter was reviewable down to the byte while the attachment beside it
was a filename in a footer.

Three routes, all about the SAME file: the path in `RESUME_PATH`. No
request names a path, so there is nothing to traverse; `GET /api/resume`
serves exactly the file `send_approved` will attach, and `POST` replaces
it in place after keeping a timestamped copy of what was there.

What this deliberately does NOT do is re-parse the resume. The claims the
drafter is held to (`resume_profile`) come from `parse-resume`, not from
this file, so replacing the PDF changes what a founder receives and not
what the model may say. Saying so is `GET /api/resume/status`'s job -
silently letting the two drift apart would be worse than either.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import Repos, get_repos
from app.config import get_operator_settings

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


def _resume_path() -> Path | None:
    configured = get_operator_settings().resume_path
    return Path(configured) if configured else None


@router.get("/resume/status", response_model=ResumeStatusOut)
def resume_status(repos: Repos = Depends(get_repos)) -> ResumeStatusOut:
    path = _resume_path()
    profile = repos.resume_profile.get_current()
    claims = len(getattr(profile, "claims", None) or []) if profile is not None else 0
    if path is None or not path.is_file():
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
    """
    path = _resume_path()
    if path is None or not path.is_file():
        raise HTTPException(404, "no resume file is configured")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@router.post("/resume", response_model=ResumeStatusOut)
async def replace_resume(
    file: UploadFile = File(...), repos: Repos = Depends(get_repos)
) -> ResumeStatusOut:
    """Replace the attached resume, keeping the old one beside it.

    The upload is checked before anything is written: a PDF header and a
    size bound. The previous file is renamed rather than deleted, because
    overwriting the only copy of a resume on a mis-click is not a failure
    this app should be capable of.
    """
    path = _resume_path()
    if path is None:
        raise HTTPException(400, "RESUME_PATH is not set in backend/.env")

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
    return resume_status(repos)
