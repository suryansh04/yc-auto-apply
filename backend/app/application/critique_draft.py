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

**`concerns` is bounded to at most `MAX_CONCERNS` (3) items here, in code
— not via a JSON-schema `maxItems` on the adapters' structured-output
schemas (fix round 2, ruling T13-3).** Gemini's `response_schema` field
(the legacy OpenAPI subset both adapters are built on) is documented to
reject `maxItems` on `ARRAY` types, and OpenAI-style strict `json_schema`
mode (which the Sarvam endpoint follows) excludes it from its supported
keyword subset too — either would risk breaking structured-output
acceptance on the first real call, and every adapter test in this repo is
mocked, so nothing offline could have caught that live rejection. The
prompt still asks the model for "0 to 3 objects"; this module is what
actually guarantees it, provider-independently, by truncating rather than
trusting the model to have counted correctly.
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

# spec: "0-3 items" (fix round 2, ruling T13-3: enforced here by
# truncation, never via a schema maxItems — see module docstring).
MAX_CONCERNS = 3


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
    concerns = raw.get("concerns")
    if isinstance(concerns, list) and len(concerns) > MAX_CONCERNS:
        # Truncate, don't reject — the critic is advisory only (D22); a
        # model that miscounted still gets its verdict and note stored,
        # just with only the first MAX_CONCERNS concerns kept, in order.
        concerns = concerns[:MAX_CONCERNS]
    return CritiqueResult(
        verdict=verdict,
        concerns=concerns,
        overall_note=raw.get("overall_note"),
    )
