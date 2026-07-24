def test_cors_header_present_on_health(client):
    resp = client.get("/api/health", headers={"Origin": "http://example.test"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_cors_preflight_allowed(client):
    resp = client.options(
        "/api/auth/login",
        headers={
            "Origin": "http://example.test",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "*"
