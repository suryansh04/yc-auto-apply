"""CORS is one of the few defenses this API has - there is no auth (spec
A3, deliberate). `TestClient` never sends an `Origin` header unless asked,
so these send one explicitly (T3 fix round 2, point 1: mutation testing
showed widening `ALLOWED_ORIGINS` to `["*"]` and adding
`allow_credentials=True` both left every other test green)."""


def test_cors_echoes_back_an_allowed_vite_origin(client):
    r = client.get("/api/stats", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_does_not_allow_an_untrusted_origin(client):
    r = client.get("/api/stats", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_never_allows_credentials(client):
    r = client.get("/api/stats", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-credentials" not in r.headers
