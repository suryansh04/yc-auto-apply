"""Response DTOs for the API adapter (Task 3). Routers translate repo/domain
rows into these; nothing here decides anything — that stays in
`app/application/`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class QuotaOut(BaseModel):
    provider: str
    configured: bool
    daily_limit: int


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    created_by: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    done: int
    total: int | None = None
    summary: str | None = None
    error: str | None = None


def to_job_out(job) -> JobOut:
    """The ONE `jobs` row -> `JobOut` translation (final fix wave, F8),
    used by both `GET /api/jobs` (`routers.jobs`) and `GET /api/stats`
    (`routers.stats`) so the two listings can never drift on a field.
    Explicit attribute reads rather than `from_attributes=True`: a row
    (or a `FakeJobRepo` row) missing one of these is a bug worth an
    `AttributeError`, not a silent default."""
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        created_by=job.created_by,
        started_at=job.started_at,
        finished_at=job.finished_at,
        done=job.done,
        total=job.total,
        summary=job.summary,
        error=job.error,
    )


class JobStartIn(BaseModel):
    """Body of `POST /api/jobs/{kind}` (Task 6, ruling PF-2). Optional -
    a bare POST with no body defaults to `created_by="ui"`, the browser's
    own case; `"schedule"` is exercised by Task 19's scheduler and `"cli"`
    is reserved for completeness (the `jobs.created_by` CHECK constraint
    allows exactly these three)."""

    created_by: Literal["ui", "schedule", "cli"] = "ui"


class JobStartOut(BaseModel):
    job_id: int


class StatsOut(BaseModel):
    counts: dict[str, int]
    quota: QuotaOut
    jobs: list[JobOut]


class QueueItemOut(BaseModel):
    """Task 4: one `PENDING_APPROVAL` row for the browser review screen -
    the same `ReviewItem` the CLI renders, translated to JSON. `wire_text`
    is the composed preview (`compose_wire_text`), never the bare `body`
    (ruling R2) - the browser reviewer must see exactly what will be sent."""

    outreach_id: int
    company_name: str | None = None
    company_one_liner: str | None = None
    founder_name: str
    founder_title: str | None = None
    to: str | None = None
    subject: str | None = None
    body: str
    wire_text: str
    word_count: int
    source_kind: str | None = None
    source_text: str | None = None
    source_publication: str | None = None
    last_error: str | None = None
    critic_verdict: str | None = None
    critic_note: str | None = None
    sequence_step: int
    # Live finding (2026-09-16): the operator reviewed a draft and said
    # "there is no resume attached" - it is attached at send time
    # (`send_approved` passes `settings.resume_path`), the preview just never
    # said so. Basename only (never the operator's filesystem path); None
    # when RESUME_PATH is unset, which is exactly the case `send` refuses.
    attachment_name: str | None = None
    # The generic company paragraph as it would read for THIS company, so
    # the screen can show it on hover before the operator commits to the
    # swap (operator request, 2026-09-23). Sent from here rather than
    # written into the JavaScript because the copy lives in
    # `app.domain.letter` and must have exactly one home - the preview and
    # what `generic-line` actually writes are the same string.
    generic_line: str | None = None


class EditIn(BaseModel):
    subject: str
    body: str


class ApplicationOut(BaseModel):
    """One application on the Applications screen.

    `outcome` is the bucket (`app.api.routers.applications.OUTCOMES`), not
    the state: `sent`, `replied`, `closed` and `bounced` are four states
    and one outcome, and `pending_approval` is one state and two outcomes
    depending on whether it was skipped. `state` comes along anyway, so
    the raw truth is never hidden behind the summary."""

    outreach_id: int
    company_name: str | None = None
    founder_name: str
    to: str | None = None
    state: str
    outcome: str
    subject: str | None = None
    sent_at: str | None = None
    skipped_at: str | None = None
    last_error: str | None = None


class CheckIn(BaseModel):
    """Optional text to judge instead of the stored draft, so the operator
    can ask "is this any good?" about something he is still typing."""

    subject: str | None = None
    body: str | None = None


class HighlightOut(BaseModel):
    """One note, plus the exact words it is about.

    `quote` is a verbatim substring of the draft body, or None when the
    note is about the letter as a whole (its length, its shape) or its
    quote could not be resolved. The screen wraps those characters and
    nothing else - marking the wrong words would be worse than marking
    none."""

    message: str
    quote: str | None = None


class SuggestionOut(BaseModel):
    """One rewrite for one highlighted span.

    `index` is the position in the same `highlights` list `/check`
    returned, so the screen pairs a rewrite with its note without matching
    on text. `quote` is repeated here so a hover card has everything it
    needs in one object. `replacement` is the new text for that span
    alone; an empty string is a real suggestion - it means delete it."""

    index: int
    quote: str
    message: str
    replacement: str
    why: str = ""


class SuggestOut(BaseModel):
    """What the model would write instead, for the spans the checks
    flagged. Advice on top of advice: the route writes nothing, refuses
    nothing, and a provider that is unconfigured or failing yields an
    empty list rather than an error - the checks are already on screen and
    must stay there.

    `unavailable` names the missing SETTING when no rewriter could be
    built (never its value), so the screen can say why the hover cards are
    empty instead of looking broken."""

    suggestions: list[SuggestionOut] = []
    unavailable: str | None = None


class CheckOut(BaseModel):
    """What the checks say about a draft. Advice, not a gate (operator
    decision, 2026-09-22): the route writes nothing and refuses nothing -
    the human decides what to do about it.

    `notes` is the plain list; `highlights` is the same notes paired with
    the text each one objects to. Both are sent so a caller that only
    wants the sentences is not forced to unwrap anything."""

    notes: list[str] = []
    highlights: list[HighlightOut] = []


class OkOut(BaseModel):
    ok: bool = True


class ManualCandidateOut(BaseModel):
    """Task 5: one already-judged `email_candidate` row, as `list-manual`
    prints it — address plus the verdict the verifier gave it, in priority
    order (the published-email hint is excluded here; it is
    `ManualItemOut.published_hint`)."""

    address: str
    verdict: str | None = None


class ManualItemOut(BaseModel):
    """Task 5: one `NEEDS_MANUAL_EMAIL` founder for the browser's manual
    queue - the same `ManualItem` the CLI's `list-manual` renders."""

    outreach_id: int
    founder_name: str
    company_name: str | None = None
    domain: str | None = None
    linkedin_url: str | None = None
    candidates: list[ManualCandidateOut]
    published_hint: str | None = None


class ManualEmailIn(BaseModel):
    """Body of `POST /api/outreach/{id}/email`: exactly one of `address`
    (store it and move to READY_TO_DRAFT) or `give_up` (move to
    UNRESOLVED) - the same choice `set-email ADDRESS` / `set-email
    --unresolved` gives the CLI."""

    address: str | None = None
    give_up: bool = False


class SendIn(BaseModel):
    """Body of `POST /api/send`: `limit` caps this one run on top of the
    rolling 24h `daily_send_cap`, same as the CLI's `--limit`.

    `outreach_id` narrows the run to one letter (the per-letter Send on
    the Approved screen). Every other rule still applies - it is a
    filter, not a bypass."""

    limit: int | None = None
    outreach_id: int | None = None


class SendResultOut(BaseModel):
    """Task 5: `SendApproved.run`'s `SendResult`, translated verbatim -
    field names match the dataclass so nothing is renamed in translation."""

    sent: int
    skipped_cap: int
    skipped_not_reservable: int
    needs_manual_check: int
    errors: int


class MessageOut(BaseModel):
    """Task 10: one message row in a thread's timeline — outbound (an
    outreach step we sent) or inbound (a founder's reply). `label`/
    `confidence`/`rationale` are `None` on every outbound row and on an
    inbound row `ClassifyReply` hasn't classified yet. `corrected` is
    `True` only once an operator has hand-corrected this row's label via
    `POST /api/messages/{id}/label` — never set by the automatic
    classifier — so the browser (Task 17) can show which labels are the
    model's own and which an operator overrode."""

    id: int
    direction: str
    sequence_step: int
    subject: str | None = None
    body: str
    sent_at: datetime | None = None
    received_at: datetime | None = None
    label: str | None = None
    confidence: float | None = None
    rationale: str | None = None
    corrected: bool = False


class ThreadOut(BaseModel):
    """Task 10: the operator's whole-conversation read view for one
    outreach — `GET /api/outreach/{id}/thread`. `messages` is every
    outbound and inbound row for the outreach, in time order (outbound
    sorted by `sent_at`, inbound by `received_at`) so the browser can
    render it as a single timeline with no client-side merge/sort logic.
    `gmail_url` is a deep link into the operator's own Gmail
    (`https://mail.google.com/mail/u/0/#inbox/<thread_id>`), `None` only
    when no message in the thread carries a Gmail thread id yet (e.g. a
    draft that was never sent)."""

    outreach_id: int
    company_name: str | None = None
    founder_name: str | None = None
    to: str | None = None
    state: str
    gmail_url: str | None = None
    messages: list[MessageOut]


class LabelCorrectionIn(BaseModel):
    """Body of `POST /api/messages/{id}/label`: the operator's corrected
    label — validated against `app.domain.replies.LABELS` by the router
    (422 on anything else, spec's six labels only)."""

    label: str


class ContactOut(BaseModel):
    """Task 5: one company/founder pair for the browser's contacts list -
    every outreach row, unfiltered by default (D19: `hiring` is a view
    control, never a gate on who is listed)."""

    outreach_id: int
    founder_name: str
    company_name: str | None = None
    domain: str | None = None
    state: str
    batch: str | None = None
    team_size: int | None = None
    is_hiring: bool = False
    resolved_email: str | None = None
    email_status: str


class GmailStatusOut(BaseModel):
    """What the UI gates on before showing the dashboard. Deliberately
    says nothing about the token itself - only whether one exists, for
    which account (which the operator configured), and whether it covers
    the read scope the reply poller needs."""

    configured: bool
    missing_config: list[str] = []
    connected: bool
    account: str | None = None
    can_read_replies: bool = False


class GmailConnectOut(BaseModel):
    auth_url: str
