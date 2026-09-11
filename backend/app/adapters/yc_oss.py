"""`YCDirectory` adapter over the public yc-oss dataset (spec §3/§9).

Fetches the full YC company list as plain JSON from a static, unauthenticated
endpoint — no API key, no rate limit concerns beyond being a polite client.
Implements the `app.ports.YCDirectory` Protocol structurally (no inheritance
needed): `fetch_companies() -> list[dict]`.
"""

import httpx

YC_OSS_COMPANIES_URL = "https://yc-oss.github.io/api/companies/all.json"
_TIMEOUT_SECONDS = 30


class YCOSSDirectory:
    def __init__(self, url: str = YC_OSS_COMPANIES_URL, timeout: float = _TIMEOUT_SECONDS):
        self._url = url
        self._timeout = timeout

    def fetch_companies(self) -> list[dict]:
        response = httpx.get(self._url, timeout=self._timeout)
        response.raise_for_status()
        return response.json()
