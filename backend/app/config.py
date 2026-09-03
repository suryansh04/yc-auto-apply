from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    reoon_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash"
    gemini_tier: Literal["free", "paid"] = "free"
    daily_send_cap: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
