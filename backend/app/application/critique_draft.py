"""Task 13: the advisory voice critic step (spec "The voice critic (D22)").

Pure application logic — depends only on the `Critic` port (`app.ports`),
never a concrete adapter, so this module never imports `app.adapters`.
`DraftOutreach` (`app.application.draft_outreach`) is the only caller: it
runs `run_critic` once per draft that has already passed the deterministic
validator (`app.domain.validation.validate_draft`), never on a draft that
hasn't.

**The single rule that governs this module:** the critic is advisory only
(D22). It can never block a send and can never approve one — it only ever
adds information for the human review step, which stays the sole gate.
Concretely that means `run_critic` never raises: a `critic.critique(...)`
call that throws (bad key, malformed response, network failure, anything)
is caught here, logged by *exception type name only* (never `str(exc)`,
which could carry provider detail — same discipline as the drafter-failure
handling in `draft_outreach.py`), and turned into an honest
`CritiqueResult` rather than a fabricated `PASS`. `verdict="ERROR"` is the
marker chosen for that case (distinct from the `verdict=None` a founder's
message row carries when no critic was configured at all — Task 13 disclosed
guess: the brief allows either `None` or an explicit marker here, and a
distinct marker is more honest to a human reviewer than reusing the
"critic never ran" value for "critic crashed").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The only verdict that ever triggers a re-draft (spec: "On NEEDS_REVISION:
# exactly one automatic re-draft"). Anything else — PASS, an unrecognised
# string, ERROR — is treated as final: the draft moves on as-is.
NEEDS_REVISION = "NEEDS_REVISION"
PASS = "PASS"
ERROR = "ERROR"  # this module's own marker for "the critic call failed"


@dataclass
class CritiqueResult:
    verdict: str | None
    concerns: list | None
    overall_note: str | None

    @property
    def needs_revision(self) -> bool:
        return self.verdict == NEEDS_REVISION


def run_critic(critic, draft: dict) -> CritiqueResult:
    """Calls `critic.critique(draft)` and normalises whatever comes back
    into a `CritiqueResult`. `draft` must already be the *validated* draft
    dict (`app.application.draft_outreach._draft_dict`) — the critic judges
    naturalness and concision on a draft that has already cleared grounding
    and length checks, never the raw, unvalidated model output.

    Never raises (see module docstring): any exception from `critique`
    becomes `CritiqueResult(verdict="ERROR", concerns=None,
    overall_note=None)` instead of propagating."""
    try:
        raw = critic.critique(draft)
    except Exception as exc:  # noqa: BLE001 — advisory pass, must never block a draft
        log.warning("critic failed: %s", type(exc).__name__)
        return CritiqueResult(verdict=ERROR, concerns=None, overall_note=None)

    raw = raw if isinstance(raw, dict) else {}
    verdict = str(raw.get("verdict") or "")
    return CritiqueResult(
        verdict=verdict,
        concerns=raw.get("concerns"),
        overall_note=raw.get("overall_note"),
    )
