from app.config import Settings


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
    assert s.resume_path == ""
    assert s.gmail_client_secrets_path == ""
    assert s.gmail_sender_address == ""


def test_email_verifier_key_is_provider_neutral():
    # D30: named for the role, not the vendor, so swapping providers
    # (Reoon -> MyEmailVerifier) touches no configuration.
    s = Settings(database_url="postgresql://x/y", email_verifier_api_key="k")
    assert s.email_verifier_api_key == "k"
    assert not hasattr(s, "reoon_api_key")
