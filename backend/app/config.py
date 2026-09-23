import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Where the resume lives when RESUME_PATH says nothing: `backend/resume/`,
# beside this file rather than relative to whatever directory the process
# happened to start in. Operator request (2026-09-23): a brand-new user
# opens My resume, presses Upload, and it works - editing .env before the
# app will accept a PDF is a step nothing in the browser could do for
# them. It is a LOCATION, not a permission: `send` still refuses until a
# file actually exists there (`missing_send_config`), so a default path
# cannot let an email go out with no attachment.
DEFAULT_RESUME_PATH = str(Path(__file__).resolve().parents[1] / "resume" / "resume.pdf")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    email_verifier_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_tier: Literal["free", "paid"] = "free"
    # LLM provider switch (2026-09-13): Gemini's free tier is 20 requests/day
    # per model, unusable at outreach volume; Sarvam (Indian pricing, UPI)
    # serves the same Drafter/ResumeParser ports. Gemini stays the default
    # so existing configs behave unchanged.
    llm_provider: Literal["gemini", "sarvam"] = "gemini"
    sarvam_api_key: str = ""
    sarvam_model: str = "sarvam-105b-conversations"
    daily_send_cap: int = 20

    # Task 15 (sending). Every default below is EMPTY on purpose: `send`
    # refuses to build a sender until all four are set, so no configuration
    # gap can let a real email go out with a blank signature, no resume, or
    # an unknown sender address.
    signature: str = ""                   # sign-off appended at send time (ruling R2, spec §18)
    resume_path: str = ""                 # the PDF attached to every outreach email (see below)
    gmail_client_secrets_path: str = ""   # OAuth "Desktop app" client JSON from Google Cloud
    gmail_sender_address: str = ""        # the Gmail account that was authorised by `gmail-auth`

    # Where the browser is sent back to after Google's consent screen.
    # The Vite dev server by default; set it if the UI is served anywhere
    # else. Localhost only, like everything else here (spec A3).
    app_url: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _default_resume_path(self) -> "Settings":
        """Blank RESUME_PATH means `backend/resume/resume.pdf`.

        The other three send settings stay empty-on-purpose: a signature,
        a Gmail address and an OAuth client are things only the operator
        knows. A file location is not - and leaving this one blank used to
        make the Upload button on My resume answer "RESUME_PATH is not set
        in backend/.env", which is a dead end a beginner cannot get out of
        from the browser.
        """
        if not self.resume_path.strip():
            self.resume_path = DEFAULT_RESUME_PATH
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def get_operator_settings() -> Settings:
    """`Settings` for reading operator preferences (signature, resume path,
    API keys, caps) where no database is needed - e.g. the API's GET
    /api/stats and /api/queue. Same `.env`, same precedence as
    `get_settings()`; the only difference is `DATABASE_URL` is not required.

    Live finding (2026-09-16): those routes read `os.environ` directly to
    avoid `Settings.database_url`'s required-ness, which silently ignored
    everything in `backend/.env` under uvicorn - the review queue previewed
    drafts WITHOUT the signature that `send` then appended.
    """
    return Settings(database_url=os.environ.get("DATABASE_URL", "unused"))
