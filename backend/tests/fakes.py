from datetime import datetime, timezone


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
                raise RuntimeError("quota exhausted")
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


class FakeSender:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, body, attachment_path):
        self.sent.append({"to": to, "subject": subject, "body": body})
        n = len(self.sent)
        return (f"msg-{n}", f"thread-{n}")


class FrozenClock:
    def __init__(self, at: datetime | None = None):
        self._at = at or datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._at
