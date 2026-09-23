from datetime import datetime, timezone

from app.ports import QuotaExhausted


class FakeYCDirectory:
    def __init__(self, companies: list[dict] | None = None):
        self._companies = companies or []

    def fetch_companies(self) -> list[dict]:
        return self._companies


class FakeCompanyPageSource:
    def __init__(self, pages: dict[str, dict] | None = None):
        self._pages = pages or {}
        self.calls: list[str] = []

    def fetch_page(self, slug: str) -> dict:
        self.calls.append(slug)
        return self._pages.get(slug, {})


class FakeResumeParser:
    def __init__(self, claims: list[dict] | None = None):
        self._claims = claims if claims is not None else []
        self.calls: list[str] = []

    def parse(self, raw_text: str) -> list[dict]:
        self.calls.append(raw_text)
        return self._claims


class FakeVerifier:
    def __init__(self, verdicts: dict[str, str], quota: int | None = None):
        self._verdicts, self._quota, self.calls = verdicts, quota, []

    def verify(self, address: str) -> str:
        if self._quota is not None:
            if self._quota <= 0:
                # Fix round 1: raises the real `QuotaExhausted` type
                # (`app.ports`) rather than a bare `RuntimeError`, so
                # `ResolveEmails.run` can catch exactly that type instead
                # of a broad `except (QuotaExhausted, RuntimeError)` that
                # would mask unrelated bugs as a quota pause.
                raise QuotaExhausted("quota exhausted")
            self._quota -= 1
        self.calls.append(address)
        return self._verdicts.get(address, "invalid")

    def remaining_quota(self) -> int | None:
        return self._quota


class FakeDrafter:
    def __init__(self, responses: list[dict] | None = None):
        self._responses = responses or []
        self.calls: list[dict] = []

    def draft(self, prompt_context: dict) -> dict:
        self.calls.append(prompt_context)
        if self._responses:
            return self._responses.pop(0)
        return {"subject": "Backend intern at Acme", "body": "Would you take an intern?",
                "research_source_type": "news", "research_source_id": 1}


class FakeCritic:
    """Task 13 (D22): fake `Critic` port. `.queue` is consumed in order,
    one verdict dict per `critique()` call; once empty, `.next` (a single
    dict, left in place across calls — unlike `.queue`, re-assign it to
    change the answer) is returned instead; with neither set, defaults to
    a plain PASS so a test that doesn't care about the critic's answer
    still gets one. `.calls` records every `draft` dict handed in, so a
    test can assert the critic saw the validated draft (exactly
    `{subject, body, research_source_type, research_source_id}`), not the
    raw model output or anything else."""

    def __init__(self):
        self.calls: list[dict] = []
        self.next: dict | None = None
        self.queue: list[dict] = []

    def critique(self, draft: dict) -> dict:
        self.calls.append(draft)
        if self.queue:
            return self.queue.pop(0)
        if self.next is not None:
            return self.next
        return {"verdict": "PASS", "concerns": [], "overall_note": "fine"}


class FakeExplodingCritic:
    """Task 13: a `Critic` whose `critique()` always raises — for the
    guarantee that a critic failure never blocks or corrupts a draft
    (D22). `.calls` still records the draft it was handed, the same as
    `FakeCritic`, so a test can confirm it was actually invoked before it
    blew up."""

    def __init__(self):
        self.calls: list[dict] = []

    def critique(self, draft: dict) -> dict:
        self.calls.append(draft)
        raise RuntimeError("critic exploded")


class FakeReplyClassifier:
    """Task 9: `.next` queues the dict `classify()` returns on its NEXT
    call (cleared after use, so a test can set a different label per call
    by re-assigning it); with no `.next` set, defaults to UNCLEAR. `.calls`
    records every context dict handed in, so a test can assert on what the
    use case actually showed the classifier (e.g. `our_message` +
    `reply_body`, not just the reply alone)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.next: dict | None = None

    def classify(self, context: dict) -> dict:
        self.calls.append(context)
        if self.next is not None:
            return self.next
        return {"label": "UNCLEAR", "confidence": 0.5, "rationale": "no queued response"}


class ExplodingReplyClassifier:
    """Task 9: every call to `classify()` raises — the classifier-failure
    path (`ClassifyReply` must leave the row unlabelled and count
    `result.errors`, never crash the whole run)."""

    def __init__(self):
        self.calls: list[dict] = []

    def classify(self, context: dict) -> dict:
        self.calls.append(context)
        raise RuntimeError("simulated classifier failure")


class FlakyReplyClassifier:
    """Task 9: raises on the FIRST call only, then answers UNCLEAR for
    every call after — proves one bad reply in a batch does not stop
    `ClassifyReply` from finishing the rest."""

    def __init__(self):
        self.calls: list[dict] = []
        self._raised = False

    def classify(self, context: dict) -> dict:
        self.calls.append(context)
        if not self._raised:
            self._raised = True
            raise RuntimeError("simulated classifier failure")
        return {"label": "UNCLEAR", "confidence": 0.3, "rationale": "recovered"}


class FakeSender:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, body, attachment_path, html=None):
        self.sent.append({"to": to, "subject": subject, "body": body, "html": html})
        n = len(self.sent)
        return (f"msg-{n}", f"thread-{n}")


class FrozenClock:
    def __init__(self, at: datetime | None = None):
        self._at = at or datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._at
