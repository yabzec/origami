from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.spa import register_spa


def make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>origami spa</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "favicon.ico").write_bytes(b"icon")
    (dist / "robots.txt").write_text("User-agent: *\nDisallow:")
    return dist


def test_spa_serves_index_and_assets(tmp_path):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    register_spa(app, make_dist(tmp_path))
    client = TestClient(app)

    assert "origami spa" in client.get("/").text
    assert "origami spa" in client.get("/documents/abc").text  # SPA fallback
    assert client.get("/favicon.ico").content == b"icon"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/nope").status_code == 404


def test_spa_serves_real_file_at_dist_root(tmp_path):
    """A legitimate file living directly at the dist root (not under assets/)
    must still be served correctly by the fixed containment check."""
    app = FastAPI()
    register_spa(app, make_dist(tmp_path))
    client = TestClient(app)

    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Disallow" in resp.text


def test_spa_blocks_path_traversal(tmp_path):
    """Regression test for the containment check in spa_fallback.

    NOTE: a plain `client.get("/../secret.txt")` does NOT exercise the
    containment check at all: httpx's TestClient normalizes ".." segments
    client-side before the request is ever sent, so the ASGI app receives
    full_path="secret.txt" (verified empirically) and the test would pass
    identically whether or not the `is_relative_to` guard exists.

    URL-encoded traversal sequences (%2e%2e) survive that client-side
    normalization and arrive at the handler as a literal "../secret.txt"
    full_path (also verified empirically), which is what actually drives
    the containment check. Confirmed by running these exact payloads
    against a scratch copy of the handler with the `is_relative_to` guard
    stripped out: the secret leaked for every payload below; with the
    guard restored (the real code), it does not.
    """
    app = FastAPI()
    register_spa(app, make_dist(tmp_path))
    client = TestClient(app)

    # a file that exists OUTSIDE dist, at a path a traversal could reach
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("do not serve me")

    for payload in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt"):
        resp = client.get(payload)
        assert b"do not serve me" not in resp.content, payload
        # falls through to the SPA index fallback rather than escaping dist
        assert "origami spa" in resp.text, payload

    # legitimate in-dist files must still be served correctly
    resp = client.get("/favicon.ico")
    assert resp.content == b"icon"
