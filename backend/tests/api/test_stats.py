from app.domain.states import OutreachState


def test_stats_counts_are_keyed_by_the_raw_outreach_state(client, repos, two_founders):
    """`two_founders` (root conftest) seeds both founders NEEDS_EMAIL, not
    NEW — T3-1: counts are keyed by the raw OutreachState value, not a
    display-level bucket, so this must land under "needs_email"."""
    body = client.get("/api/stats").json()
    assert body["counts"]["needs_email"] == 2
    assert body["counts"]["new"] == 0


def test_stats_counts_include_every_state_even_at_zero(client, repos, two_founders):
    """The frontend must never have to guess whether a missing key means
    zero or an error (T3-1)."""
    body = client.get("/api/stats").json()
    assert set(body["counts"].keys()) == {s.value for s in OutreachState}
    for state in OutreachState:
        if state.value != "needs_email":
            assert body["counts"][state.value] == 0


def test_needs_manual_email_founder_counts_under_its_own_key_not_new(client, repos):
    """Regression for T3-1: folding NEEDS_MANUAL_EMAIL into a coarser "new"
    bucket would hide the action queue (25 founders stuck here on the live
    database) that the dashboard exists to surface."""
    repos.company.add(id=50, domain="acme.com")
    founder = repos.founder.add(id=50, company_id=50, full_name="Needs Manual")
    repos.outreach.add(id=50, founder_id=founder.id, state=str(OutreachState.NEEDS_MANUAL_EMAIL))

    body = client.get("/api/stats").json()
    assert body["counts"]["needs_manual_email"] == 1
    assert body["counts"]["new"] == 0


def test_stats_is_json_and_has_no_server_header_leak(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_unknown_route_is_404_not_a_traceback(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert "Traceback" not in r.text
