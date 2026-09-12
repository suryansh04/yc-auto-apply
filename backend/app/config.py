from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    sarvam_model: str = "sarvam-105b"
    daily_send_cap: int = 20

    # Task 15 (sending). Every default below is EMPTY on purpose: `send`
    # refuses to build a sender until all four are set, so no configuration
    # gap can let a real email go out with a blank signature, no resume, or
    # an unknown sender address.
    signature: str = ""                   # sign-off appended at send time (ruling R2, spec §18)
    resume_path: str = ""                 # the PDF attached to every outreach email
    gmail_client_secrets_path: str = ""   # OAuth "Desktop app" client JSON from Google Cloud
    gmail_sender_address: str = ""        # the Gmail account that was authorised by `gmail-auth`


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
