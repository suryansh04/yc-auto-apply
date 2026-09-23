"""Getting the words out of a resume file.

A file-format concern, not a decision: `ParseResume`
(`app.application.parse_resume`) is handed text and never learns where it
came from.

Extracted from `app.cli._read_resume_text` on 2026-09-23, when parsing
stopped being CLI-only - `app.api.routers.resume` needs exactly the same
extraction, and `app/api/` may not import `app.cli` (the CLI is the
operator adapter, not a library for the API layer, per
`tests/test_architecture.py`). The same extraction for both callers is
also the point: a resume that parses from the terminal and not from the
browser would be a bug nobody could explain.
"""

from __future__ import annotations

from pathlib import Path


def read_resume_text(path: str) -> str:
    """PDF via `pypdf` (text extraction only - no OCR; a scanned-image
    resume yields empty text and is rejected downstream), or a plain
    `.txt`/`.md` read for anything else."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return p.read_text(encoding="utf-8")
