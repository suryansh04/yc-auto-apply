"""One-off: move already-drafted letters onto the 2026-09-23 blocks.

The letter in `app.domain.letter` was rewritten on 2026-09-23 from the
operator's second example email. Everything drafted before that carries
the old blocks - the "Now Here's the Important Part:" hinge, the
"I can see that you have great users and growth" ask - and nothing
downstream can find the company paragraph in them any more, because the
anchors it is found by no longer exist there.

Redrafting would fix it and cost one model call per letter. It would also
throw away a researched, reviewed company paragraph and roll the dice on
a new one. So this takes the OLD anchors apart instead and re-assembles
the same company paragraph into the NEW blocks: no model call, no new
claim about anyone, and a letter that comes out the other side saying
exactly what it said about the founder before.

Safe by construction:

- It only ever recognises a body that still has BOTH old anchors around
  a non-empty middle. Anything else - already migrated, hand-written,
  restructured - is left untouched and reported as skipped.
- It only touches PENDING_APPROVAL and APPROVED letters. A sent letter is
  a historical record of what the founder actually received and must
  never be rewritten.
- `--apply` is required to write. The default prints what would change.

    python -m scripts.reletter            # show what would change
    python -m scripts.reletter --apply    # do it
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.adapters.db.repositories import CompanyRepo, FounderRepo, MessageRepo, OutreachRepo
from app.config import get_settings
from app.domain import letter
from app.domain.states import OutreachState

# The old blocks, copied here verbatim rather than imported: they are gone
# from `app.domain.letter`, and this script has to keep recognising them
# after they have been gone for months. A migration that reads the current
# copy of the letter would stop working the moment the letter changes
# again, which is the bug it exists to fix.
OLD_SECTION_BREAK = "Now Here's the Important Part:"
OLD_ASK = (
    "I can see that you have great users and growth, these users come with "
    "issues they face. I'd like to be one of the people fixing it as an intern."
)

MIGRATABLE = (OutreachState.PENDING_APPROVAL, OutreachState.APPROVED)


def old_insight(body: str) -> str | None:
    """The company paragraph(s) out of an old-shaped body, or None."""
    text = body or ""
    if OLD_SECTION_BREAK not in text or OLD_ASK not in text:
        return None
    middle = text.split(OLD_SECTION_BREAK, 1)[1].split(OLD_ASK, 1)[0]
    return middle.strip() or None


def relettered(body: str, company_name: str) -> str | None:
    """The same company paragraph(s), in the new blocks. None if this body
    is not in the old shape."""
    insight = old_insight(body)
    if insight is None:
        return None
    paragraphs = [p.strip() for p in insight.split("\n\n") if p.strip()]
    # The operator's own user-fit paragraph, if he filled one in, is part
    # of the middle and comes along with it - it is his text and belongs
    # in the same place in the new letter.
    return letter.assemble_body(company_name, paragraphs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default: show them)")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url)
    changed = skipped = 0
    with Session(bind=engine) as session:
        outreach_repo = OutreachRepo(session)
        message_repo = MessageRepo(session)
        founder_repo = FounderRepo(session)
        company_repo = CompanyRepo(session)

        for state in MIGRATABLE:
            for row in outreach_repo.list_by_state(state):
                step = int(getattr(row, "sequence_step", 0) or 0)
                message = message_repo.get_outbound(row.id, step)
                if message is None or not message.body:
                    continue
                founder = founder_repo.get(row.founder_id)
                company = company_repo.get(founder.company_id) if founder else None
                name = getattr(company, "name", None) or ""
                fresh = relettered(message.body, name)
                if fresh is None:
                    skipped += 1
                    continue
                changed += 1
                print(f"  {row.id:>4}  {name}")
                if args.apply:
                    message_repo.upsert(id=message.id, body=fresh)
        if args.apply:
            session.commit()

    verb = "rewritten" if args.apply else "would be rewritten"
    print(f"\n{changed} {verb}, {skipped} left alone (not in the old shape).")
    if not args.apply and changed:
        print("Re-run with --apply to write them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
