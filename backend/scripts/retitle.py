"""One-off: drop the bridge line and put the operator's subject on the
letters already drafted.

Operator request (2026-09-23), after `scripts/reletter.py` had already
moved these letters onto the current blocks:

- "That's the kind of problem I'd genuinely like to work on." comes out.
  It was a line about the company paragraph rather than a line of its
  own, and the paragraph ends on what he wants to build anyway.
- The subject becomes "Would love to build at {company}", written down
  in `app.domain.letter` instead of asked of a model.

Both are textual and neither touches the company paragraph, so no model
call is needed and nothing a human reviewed is thrown away.

Safe by construction, the same way `reletter.py` is:

- The bridge is removed only when it is there, as its own paragraph.
  Anything else is left exactly as it is.
- The subject is only replaced when the letter is one of ours - it has
  the assembled shape - and the company has a name to put in it.
- Only PENDING_APPROVAL and APPROVED letters, and only step 0. A sent
  letter is a record of what the founder actually received, and a
  follow-up has neither of these things.
- `--apply` is required to write. The default prints what would change.

    python -m scripts.retitle            # show what would change
    python -m scripts.retitle --apply    # do it
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

MIGRATABLE = (OutreachState.PENDING_APPROVAL, OutreachState.APPROVED)


def without_bridge(body: str) -> str:
    """`body` with the dropped bridge line taken out, if it is in there.

    Removed as a whole paragraph - the line plus the blank line under it -
    so the letter closes up rather than leaving a gap where it was.
    """
    text = body or ""
    if letter.OLD_BRIDGE not in text:
        return text
    for pattern in (letter.OLD_BRIDGE + "\n\n", "\n\n" + letter.OLD_BRIDGE, letter.OLD_BRIDGE):
        if pattern in text:
            return text.replace(pattern, "", 1)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default: show them)")
    args = parser.parse_args(argv)

    engine = create_engine(get_settings().database_url)
    bodies = subjects = 0
    with Session(bind=engine) as session:
        outreach_repo = OutreachRepo(session)
        message_repo = MessageRepo(session)
        founder_repo = FounderRepo(session)
        company_repo = CompanyRepo(session)

        for state in MIGRATABLE:
            for row in outreach_repo.list_by_state(state):
                if int(getattr(row, "sequence_step", 0) or 0) != 0:
                    continue
                message = message_repo.get_outbound(row.id, 0)
                if message is None or not message.body:
                    continue
                founder = founder_repo.get(row.founder_id)
                company = company_repo.get(founder.company_id) if founder else None
                name = getattr(company, "name", None) or ""

                fields = {}
                body = without_bridge(message.body)
                if body != message.body:
                    fields["body"] = body
                    bodies += 1
                # Only a letter with our shape gets our subject: a
                # hand-written one is the operator's, subject included.
                wanted = letter.subject(name) if name else None
                if (wanted and message.subject != wanted
                        and letter.insight_region(body) is not None):
                    fields["subject"] = wanted
                    subjects += 1
                if not fields:
                    continue
                print(f"  {row.id:>4}  {name}"
                      f"{'  [body]' if 'body' in fields else ''}"
                      f"{'  [subject]' if 'subject' in fields else ''}")
                if args.apply:
                    message_repo.upsert(id=message.id, **fields)
        if args.apply:
            session.commit()

    verb = "rewritten" if args.apply else "would be rewritten"
    print(f"\n{bodies} bodies and {subjects} subjects {verb}.")
    if not args.apply and (bodies or subjects):
        print("Re-run with --apply to write them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
