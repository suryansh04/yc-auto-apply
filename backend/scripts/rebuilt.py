"""One-off: move already-drafted letters onto the 2026-09-23 opener and
list of what he built.

Operator request (2026-09-23):

- The opener loses "and I promise it's not another AI-generated cold
  mail" and is now just "This'll take 45 seconds."
- The bullet list of what he built becomes three numbered items, led by
  the GitHub integration he shipped at Better.

Both are whole blocks the operator wrote (`app.domain.letter`), matched
here exactly as they were, so the company paragraph in each letter - the
researched part a human reviewed - is never touched. No model call.

Safe by construction, like `reletter.py` and `retitle.py`:

- A block is replaced only when the OLD block is present word for word.
  A letter where he edited that block by hand keeps his edit.
- Only PENDING_APPROVAL and APPROVED letters, only step 0. A sent letter
  is a record of what the founder received.
- A letter with formatting (`body_html`) is left alone and reported:
  rewriting its plain text would put it out of step with its formatting.
  Those are few, recent, and his to edit in the editor.
- `--apply` is required to write. The default prints what would change.

    python -m scripts.rebuilt            # show what would change
    python -m scripts.rebuilt --apply    # do it
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.adapters.db.repositories import MessageRepo, OutreachRepo
from app.config import get_settings
from app.domain import letter
from app.domain.states import OutreachState

MIGRATABLE = (OutreachState.PENDING_APPROVAL, OutreachState.APPROVED)
SWAPS = ((letter.OLD_OPENER, letter.OPENER), (letter.OLD_BUILT, letter.BUILT))


def rebuilt(body: str) -> str:
    """`body` with each old block that is present replaced by its new one."""
    for old, new in SWAPS:
        body = body.replace(old, new, 1)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default: show them)")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url)
    changed = formatted = 0
    with Session(bind=engine) as session:
        outreach_repo = OutreachRepo(session)
        message_repo = MessageRepo(session)
        for state in MIGRATABLE:
            for row in outreach_repo.list_by_state(state):
                if int(getattr(row, "sequence_step", 0) or 0) != 0:
                    continue
                message = message_repo.get_outbound(row.id, 0)
                if message is None or not message.body:
                    continue
                fresh = rebuilt(message.body)
                if fresh == message.body:
                    continue
                if getattr(message, "body_html", None):
                    formatted += 1
                    print(f"  {row.id:>4}  left alone - it has formatting; edit it in the editor")
                    continue
                changed += 1
                what = [name for (old, _), name in zip(SWAPS, ("opener", "list"))
                        if old in message.body]
                print(f"  {row.id:>4}  {' + '.join(what)}")
                if args.apply:
                    message_repo.upsert(id=message.id, body=fresh)
        if args.apply:
            session.commit()

    verb = "rewritten" if args.apply else "would be rewritten"
    print(f"\n{changed} {verb}; {formatted} formatted letter(s) left alone.")
    if not args.apply and changed:
        print("Re-run with --apply to write them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
