from app.config import Settings


def test_settings_reads_defaults_without_env():
    s = Settings(database_url="postgresql://x/y")
    assert s.gemini_model == "gemini-3-flash"
    assert s.gemini_tier == "free"
    assert s.daily_send_cap == 20


def test_paid_tier_is_explicit_opt_in():
    s = Settings(database_url="postgresql://x/y", gemini_tier="paid")
    assert s.gemini_tier == "paid"
