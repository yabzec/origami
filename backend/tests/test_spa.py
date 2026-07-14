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
    app = FastAPI()
    register_spa(app, make_dist(tmp_path))
    client = TestClient(app)

    # a file that exists OUTSIDE dist, at a path a traversal could reach
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("do not serve me")

    resp = client.get("/../secret.txt")
    assert b"do not serve me" not in resp.content
    # falls through to the SPA index fallback rather than escaping dist
    assert "origami spa" in resp.text
