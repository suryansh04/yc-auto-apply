import os

from app.config import DEFAULT_RESUME_PATH, Settings as _Settings


def Settings(**kw):
    # The suite must not depend on whether the operator has a real `.env`
    # in `backend/` (it did not exist during the build, it does now):
    # pin defaults by disabling the env file for these tests only.
    return _Settings(_env_file=None, **kw)


def test_settings_reads_defaults_without_env():
    s = Settings(database_url="postgresql://x/y")
    assert s.gemini_model == "gemini-3.8-flash"
    assert s.gemini_tier == "free"
    assert s.daily_send_cap == 20


def test_paid_tier_is_explicit_opt_in():
    s = Settings(database_url="postgresql://x/y", gemini_tier="paid")
    assert s.gemini_tier == "paid"


def test_send_settings_default_to_empty_so_nothing_can_send_by_accident():
    # Task 15: `send` refuses until all four are set; none may carry a
    # default that would let a real email go out from a fresh .env.
    s = Settings(database_url="postgresql://x/y")
    assert s.signature == ""
    assert s.gmail_client_secrets_path == ""
    assert s.gmail_sender_address == ""


def test_a_blank_resume_path_means_the_default_location():
    """The one send setting with a default (2026-09-23), because it is a
    LOCATION and not something only the operator knows. `send` still
    refuses until a file exists there - `missing_send_config` checks the
    file, not the setting - so this cannot let an email go out with no
    attachment; it only means the Upload button in the UI works on a
    fresh checkout, without editing .env first."""
    s = Settings(database_url="postgresql://x/y")
    assert s.resume_path == DEFAULT_RESUME_PATH
    assert s.resume_path.endswith(os.path.join("backend", "resume", "resume.pdf"))
    assert Settings(database_url="postgresql://x/y", resume_path="  ").resume_path == DEFAULT_RESUME_PATH
    assert Settings(database_url="postgresql://x/y", resume_path="mine.pdf").resume_path == "mine.pdf"


def test_email_verifier_key_is_provider_neutral():
    # D30: named for the role, not the vendor, so swapping providers
    # (Reoon -> MyEmailVerifier) touches no configuration.
    s = Settings(database_url="postgresql://x/y", email_verifier_api_key="k")
    assert s.email_verifier_api_key == "k"
    assert not hasattr(s, "reoon_api_key")
