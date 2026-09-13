def test_stats_returns_funnel_counts_by_state(client, repos, two_founders):
    body = client.get("/api/stats").json()
    assert body["counts"]["new"] == 2


def test_stats_is_json_and_has_no_server_header_leak(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_unknown_route_is_404_not_a_traceback(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert "Traceback" not in r.text
