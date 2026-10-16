"""Follow-ups for sent emails with no reply (operator request 2026-10-16).

"add a section for follow up - basically replying or following up the
already sent mail which havent received any replies ... give a
recommendation to what to send in the followup mail".

Three things, all advice until the operator approves:

- `list_follow_ups`: every SENT thread with no reply, when its next
  follow-up is due (the same `followup_day_1`/`followup_day_2` settings
  `ScheduleFollowUps` uses: 5 and 12 days by default), and any follow-up
  he has already approved that is waiting to go out.
- `suggest_follow_ups`: three suggestions from a `FollowUpWriter`
  (nudge, new angle, last note), each checked here.
- `approve_follow_up`: his chosen text becomes the next step's outbound
  message, approved, and the outreach moves SENT -> READY_TO_DRAFT ->
  PENDING_APPROVAL -> APPROVED through `assert_transition`. Sending is
  the existing `SendApproved`, which replies in the same Gmail thread.

At most two follow-ups (spec section 6); after the second the scheduler
closes the thread. Pure application logic: ports and repos only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.domain.names import company_display_name
from app.domain.replies import MACHINE_LABELS
from app.domain.states import OutreachState, assert_transition
from app.domain.validation import BANNED_PHRASES, CHECKLIST_BANNED

log = logging.getLogger(__name__)

MAX_FOLLOW_UPS = 2
DEFAULT_DAY_1 = 5
DEFAULT_DAY_2 = 12
WORDS = {1: (20, 70), 2: (15, 50)}

_FOLLOWUP_TELLS = (
    "just following up", "following up on", "circling back", "bumping this", "bump this",
    "checking in", "gentle reminder", "any update", "in case you missed", "per my last",
    "sorry to bother", "hope you're doing well", "hope you are doing well",
    "i hope this email finds you",
)
_TELLS = tuple(p.casefold() for p in (*BANNED_PHRASES, *CHECKLIST_BANNED, *_FOLLOWUP_TELLS))


@dataclass
class FollowUpItem:
    outreach_id: int
    company_name: str
    founder_name: str
    to: str | None
    subject: str
    first_email: str
    last_sent_at: datetime | None
    follow_ups_sent: int          # 0, 1 or 2
    due_at: datetime | None
    status: str                   # "due" | "waiting" | "approved" | "done"
    approved_body: str | None = None
    research: dict | None = None
    sent: list[dict] = field(default_factory=list)


def _intervals(settings_repo) -> tuple[int, int]:
    row = settings_repo.get() if settings_repo is not None else None
    day1 = getattr(row, "followup_day_1", None) or DEFAULT_DAY_1
    day2 = getattr(row, "followup_day_2", None) or DEFAULT_DAY_2
    return int(day1), int(day2)


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _sent_rows(message_repo, outreach_id: int) -> list:
    rows = [m for m in message_repo.list_by_outreach(outreach_id)
            if m.direction == "outbound" and getattr(m, "sent_at", None) is not None]
    return sorted(rows, key=lambda m: m.sequence_step)


def _has_reply(message_repo, outreach_id: int) -> bool:
    return any(m.direction == "inbound" and getattr(m, "label", None) not in MACHINE_LABELS
               for m in message_repo.list_by_outreach(outreach_id))


def list_follow_ups(repos, now: datetime) -> list[FollowUpItem]:
    """SENT threads with no human reply, plus approved follow-ups waiting
    to go out. Due ones first, then by how long they have waited."""
    day1, day2 = _intervals(getattr(repos, "settings", None))
    items: list[FollowUpItem] = []
    for state in (OutreachState.SENT, OutreachState.APPROVED):
        for founder in repos.founder.list_by_state(state=state):
            outreach = repos.outreach.get_by_founder_id(founder.id)
            if outreach is None:
                continue
            step = int(getattr(outreach, "sequence_step", 0) or 0)
            if state is OutreachState.APPROVED and step == 0:
                continue  # a first email waiting to go out - not a follow-up
            if _has_reply(repos.message, outreach.id):
                continue
            company = repos.company.get(founder.company_id)
            sent = _sent_rows(repos.message, outreach.id)
            if not sent:
                continue
            first, last = sent[0], sent[-1]
            follow_ups_sent = len(sent) - 1
            last_at = _aware(last.sent_at)
            if state is OutreachState.APPROVED:
                pending = repos.message.get_outbound(outreach.id, step)
                status, due_at, body = "approved", None, getattr(pending, "body", None)
            elif follow_ups_sent >= MAX_FOLLOW_UPS:
                status, due_at, body = "done", None, None
            else:
                due_at = last_at + timedelta(days=day1 if follow_ups_sent == 0 else day2)
                status, body = ("due" if now >= due_at else "waiting"), None
            items.append(FollowUpItem(
                outreach_id=outreach.id,
                company_name=company_display_name(getattr(company, "name", "") or ""),
                founder_name=founder.full_name,
                to=getattr(outreach, "resolved_email", None),
                subject=getattr(first, "subject", None) or "",
                first_email=first.body or "",
                last_sent_at=last_at,
                follow_ups_sent=follow_ups_sent,
                due_at=due_at,
                status=status,
                approved_body=body,
                research=getattr(first, "research", None),
                sent=[{"step": m.sequence_step, "body": m.body, "sent_at": _aware(m.sent_at)}
                      for m in sent],
            ))
    order = {"approved": 0, "due": 1, "waiting": 2, "done": 3}
    items.sort(key=lambda i: (order[i.status], i.last_sent_at or now))
    return items


def followup_problem(body: str, step: int, known: str = "") -> str | None:
    """Why this follow-up cannot be offered, or None when it can."""
    text = (body or "").strip()
    if not text:
        return "empty"
    low_w, high_w = WORDS.get(step, WORDS[2])
    words = len(text.split())
    if words < low_w:
        return f"too short ({words} words)"
    if words > high_w:
        return f"too long ({words} words)"
    if "—" in text or "–" in text or re.search(r"[*_#`]|https?://|www\.", text):
        return "not plain text, or has a dash or a link"
    if text.count("?") > 1:
        return "asks more than one question"
    first_line = text.splitlines()[0].strip().lower()
    if re.match(r"^(hi|hey|hello|dear)\b", first_line):
        return "starts with a greeting - the greeting is added for him"
    folded = text.casefold()
    for phrase in _TELLS:
        if phrase in folded:
            return f"uses {phrase!r}"
    for number in re.findall(r"\d[\d,.]*", text):
        if known and number.strip(".,") not in known:
            return f"quotes {number}, which is in none of the facts"
    return None


def suggest_follow_ups(writer, item: FollowUpItem, description: str = "",
                       avoid: list[str] | None = None, now: datetime | None = None) -> dict | None:
    """`{options: [{style, body}], why}` with at least one usable option,
    or None. Never raises."""
    if item.follow_ups_sent >= MAX_FOLLOW_UPS:
        return None
    now = now or datetime.now(timezone.utc)
    step = item.follow_ups_sent + 1
    days = (now - item.last_sent_at).days if item.last_sent_at else None
    earlier = item.sent[1]["body"] if len(item.sent) > 1 else ""
    try:
        answer = writer.write({
            "founder_first_name": (item.founder_name or "").split(" ")[0],
            "company_name": item.company_name,
            "step": step,
            "days_since": days,
            "first_email": item.first_email,
            "earlier_followup": earlier,
            "research": item.research,
            "description": description,
            "avoid": list(avoid or []),
        }) or {}
    except Exception as exc:  # noqa: BLE001 - advice that did not arrive
        log.warning("follow-up: writer failed (%s)", type(exc).__name__)
        return None
    known = " ".join([item.first_email, earlier, description,
                      *(f.get("text", "") for f in (item.research or {}).get("facts") or [])])
    seen = {a.strip().casefold() for a in (avoid or []) if a}
    usable = []
    for option in answer.get("options") or []:
        body = str(option.get("body") or "").strip()
        problem = followup_problem(body, step, known)
        if problem is None and (body.casefold() in seen
                                or any(body.casefold() == u["body"].casefold() for u in usable)):
            problem = "already suggested"
        if problem:
            log.info("follow-up: dropped a %s option - %s", option.get("style") or "?", problem)
            continue
        usable.append({"style": str(option.get("style") or ""), "body": body})
    if not usable:
        return None
    return {"options": usable, "why": str(answer.get("why") or "").strip()}


def approve_follow_up(repos, outreach_id: int, body: str, now: datetime) -> None:
    """His follow-up, approved and lined up as the next email in the
    thread. Raises ValueError with a sentence for the operator when it
    cannot be (wrong state, a reply came in, no follow-ups left)."""
    body = (body or "").strip()
    if not body:
        raise ValueError("the follow-up is empty")
    outreach = repos.outreach.get(outreach_id)
    if outreach is None:
        raise LookupError(outreach_id)
    if OutreachState(outreach.state) is not OutreachState.SENT:
        raise ValueError(f"only a sent email can be followed up (this one is {outreach.state})")
    if _has_reply(repos.message, outreach.id):
        raise ValueError("they have replied - answer the reply instead of following up")
    sent = _sent_rows(repos.message, outreach.id)
    if not sent:
        raise ValueError("nothing has been sent in this thread yet")
    step = int(getattr(outreach, "sequence_step", 0) or 0) + 1
    if step > MAX_FOLLOW_UPS:
        raise ValueError("both follow-ups have already been sent")
    # Every hop is a legal transition, checked before anything is written.
    for frm, to in ((OutreachState.SENT, OutreachState.READY_TO_DRAFT),
                    (OutreachState.READY_TO_DRAFT, OutreachState.PENDING_APPROVAL),
                    (OutreachState.PENDING_APPROVAL, OutreachState.APPROVED)):
        assert_transition(frm, to)
    original = (sent[0].subject or "").strip()
    while original.lower().startswith("re:"):
        original = original[3:].strip()
    fields = dict(
        outreach_id=outreach.id, sequence_step=step, direction="outbound",
        subject=f"Re: {original}", body=body, body_html=None,
        approved_at=now, edited_by_user=True,
    )
    existing = repos.message.get_outbound(outreach.id, step)
    if existing is not None:
        if getattr(existing, "sent_at", None) is not None:
            raise ValueError("that follow-up has already been sent")
        fields["id"] = existing.id
    repos.message.upsert(**fields)
    repos.outreach.upsert(
        founder_id=outreach.founder_id, state=str(OutreachState.APPROVED),
        sequence_step=step, next_action_at=None, last_error=None,
    )
