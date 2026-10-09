from urllib.parse import parse_qs, urlparse

import pytest
from starlette.websockets import WebSocketDisconnect

CLIENT = "0f8e2a4c-1111-4222-8333-444455556666"


def launch(auth_client, client_id=CLIENT):
    return auth_client.post("/api/agent/launch", json={"client_id": client_id})


def test_launch_returns_agent_url(auth_client, agent_hub, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("PUBLIC_URL", "https://origami.example.org")
    get_settings.cache_clear()
    try:
        resp = launch(auth_client)
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200
    url = urlparse(resp.json()["url"])
    assert url.scheme == "origami-agent" and url.netloc == "connect"
    query = parse_qs(url.query)
    assert query["server"] == ["https://origami.example.org"]
    assert agent_hub.consume_token(query["token"][0]) is not None


def test_launch_falls_back_to_request_base_url(auth_client, agent_hub):
    query = parse_qs(urlparse(launch(auth_client).json()["url"]).query)
    assert query["server"] == ["http://testserver"]


@pytest.mark.parametrize("bad", ["short", "has:colon-xxxxxxxx", "x" * 65])
def test_launch_rejects_bad_client_id(auth_client, agent_hub, bad):
    resp = launch(auth_client, bad)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_client_id"


def test_launch_requires_login(client, agent_hub):
    assert client.post("/api/agent/launch", json={"client_id": CLIENT}).status_code == 401


def test_ws_with_valid_token_registers_agent(auth_client, agent_hub, user):
    token = parse_qs(urlparse(launch(auth_client).json()["url"]).query)["token"][0]
    with auth_client.websocket_connect(f"/api/agent/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "welcome"
        assert agent_hub.connected(user.id, CLIENT)


def test_ws_rejects_bad_token(auth_client, agent_hub):
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect("/api/agent/ws?token=nope") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_resume_token_reconnects(auth_client, agent_hub, user):
    token = parse_qs(urlparse(launch(auth_client).json()["url"]).query)["token"][0]
    with auth_client.websocket_connect(f"/api/agent/ws?token={token}") as ws:
        resume = ws.receive_json()["resume_token"]
    with auth_client.websocket_connect(f"/api/agent/ws?token={resume}") as ws:
        assert ws.receive_json()["type"] == "welcome"
        assert agent_hub.connected(user.id, CLIENT)


def test_download_serves_built_file(auth_client, tmp_path, monkeypatch):
    from app.config import get_settings

    (tmp_path / "origami-agent-linux-amd64").write_bytes(b"\x7fELF-binary")
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        ok = auth_client.get("/api/agent/download/linux-amd64")
        missing = auth_client.get("/api/agent/download/windows-amd64")
        unknown = auth_client.get("/api/agent/download/amiga")
    finally:
        get_settings.cache_clear()
    assert ok.status_code == 200 and ok.content == b"\x7fELF-binary"
    assert 'filename="origami-agent-linux-amd64"' in ok.headers["content-disposition"]
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "agent_not_built"
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_platform"


def test_download_accepts_query_token(client, user, tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services.auth import create_access_token

    (tmp_path / "origami-agent-linux-amd64").write_bytes(b"bin")
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        resp = client.get(f"/api/agent/download/linux-amd64?token={create_access_token(user.id)}")
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200


def test_discover_asks_connected_agent_to_browse(auth_client, agent_hub, user):
    token = parse_qs(urlparse(launch(auth_client).json()["url"]).query)["token"][0]
    with auth_client.websocket_connect(f"/api/agent/ws?token={token}") as ws:
        ws.receive_json()
        resp = auth_client.post("/api/agent/discover", json={"client_id": CLIENT})
        assert resp.status_code == 204
        assert ws.receive_json() == {"type": "discover"}


def test_discover_without_agent_is_204(auth_client, agent_hub):
    assert auth_client.post("/api/agent/discover", json={"client_id": CLIENT}).status_code == 204


def test_discover_validates_client_id_and_login(client, auth_client, agent_hub):
    resp = auth_client.post("/api/agent/discover", json={"client_id": "short"})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "invalid_client_id"
    del auth_client.headers["Authorization"]
    assert client.post("/api/agent/discover", json={"client_id": CLIENT}).status_code == 401


def test_downloads_lists_built_platforms(auth_client, tmp_path, monkeypatch):
    from app.config import get_settings

    (tmp_path / "origami-agent-linux-amd64").write_bytes(b"bin")
    (tmp_path / "origami-agent-windows-amd64.exe").write_bytes(b"exe")
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        resp = auth_client.get("/api/agent/downloads")
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200
    assert resp.json() == {"platforms": ["windows-amd64", "linux-amd64"]}


def test_downloads_requires_login(client):
    assert client.get("/api/agent/downloads").status_code == 401
