"""`WebSearch` over Tavily (2026-09-25).

Operator request: "a web agent or web scraper where we can write something
about the founder". The YC bio is the first source and covers 229 of 284
founders; this is for the rest, and for bios too thin to say anything.

Why Tavily: a search API built for LLM agents that returns the page text
alongside each result, so there is no scraper to maintain, and its free
tier (1,000 searches a month, no card) covers this app many times over -
the operator is budget-constrained and pays in INR. LinkedIn is never
scraped: it blocks scrapers and forbids it in its terms.

The key goes in a header and never into an error, a log or a response:
an HTTP failure raises `TavilyError` naming the status only.
"""

from __future__ import annotations

import httpx

URL = "https://api.tavily.com/search"
_TIMEOUT = 20.0
_MAX_CONTENT_CHARS = 700


class TavilyError(RuntimeError):
    def __init__(self, status: int | None = None):
        self.status = status
        super().__init__(f"web search failed (HTTP {status})" if status else "web search failed")


class TavilySearch:
    def __init__(self, api_key: str, *, client: httpx.Client | None = None):
        self._key = api_key
        self._client = client or httpx.Client(timeout=_TIMEOUT)

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        try:
            response = self._client.post(
                URL,
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",   # 1 credit
                    "include_answer": False,
                },
            )
        except httpx.HTTPError:
            raise TavilyError() from None
        if response.status_code != 200:
            raise TavilyError(response.status_code)
        try:
            results = response.json().get("results") or []
        except ValueError:
            raise TavilyError(response.status_code) from None
        return [
            {
                "title": str(r.get("title") or ""),
                "url": str(r.get("url") or ""),
                "content": str(r.get("content") or "")[:_MAX_CONTENT_CHARS],
                # When Tavily knows it - it is what marks a fact as OLD.
                "published_date": str(r.get("published_date") or ""),
            }
            for r in results if isinstance(r, dict)
        ]
