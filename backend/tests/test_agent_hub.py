import threading

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.services.agent_hub import AgentHub, AgentOffline, AgentTimeout


def make_app(hub: AgentHub) -> FastAPI:
    app = FastAPI()

    @app.websocket("/ws/{user_id}/{client_id}")
    async def ws(websocket: WebSocket, user_id: int, client_id: str):
        await websocket.accept()
        await hub.serve(websocket, user_id, client_id)

    return app


def run_request(hub, *args, **kwargs):
    box = {}

    def target():
        try:
            box["result"] = hub.request(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the test
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    return t, box


def test_tokens_single_use_and_expire():
    hub = AgentHub()
    t = hub.issue_token(1, "client-abc", now=1000.0)
    assert hub.consume_token(t, now=1001.0) == (1, "client-abc")
    assert hub.consume_token(t, now=1001.0) is None
    old = hub.issue_token(1, "client-abc", now=1000.0)
    assert hub.consume_token(old, now=1121.0) is None


def test_devices_and_round_trip_with_chunks():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client, client.websocket_connect("/ws/1/client-abc") as ws:
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome" and welcome["resume_token"]
        ws.send_json({"type": "hello", "version": "0.1.0", "os": "linux"})
        ws.send_json({"type": "devices", "devices": [{"uuid": "u1", "name": "HP"}]})
        ws.send_json({"type": "noop"})  # hub ignores unknown types
        # devices are visible once processed
        for _ in range(50):
            if hub.scanners(1, "client-abc"):
                break
            threading.Event().wait(0.02)
        assert hub.connected(1, "client-abc")
        assert hub.scanners(1, "client-abc") == [{"uuid": "u1", "name": "HP"}]

        t, box = run_request(hub, 1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=5)
        msg = ws.receive_json()
        assert msg["type"] == "escl"
        assert (msg["scanner_uuid"], msg["method"], msg["path"]) == ("u1", "GET", "ScannerCapabilities")
        rid = msg["id"]
        assert len(rid) == 16
        ws.send_json({"type": "escl_response", "id": rid, "status": 200,
                      "content_type": "text/xml", "headers": {"Location": "ScanJobs/1"}, "length": 6})
        ws.send_bytes(rid.encode() + b"abc")
        ws.send_bytes(rid.encode() + b"def")
        ws.send_json({"type": "end", "id": rid})
        t.join(5)
        res = box["result"]
        assert (res.status, res.content_type, res.body) == (200, "text/xml", b"abcdef")
        assert res.headers == {"Location": "ScanJobs/1"}


def test_request_without_agent_is_offline():
    with pytest.raises(AgentOffline):
        AgentHub().request(1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=1)


def test_disconnect_fails_pending_request_at_once():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client:
        with client.websocket_connect("/ws/1/client-abc") as ws:
            ws.receive_json()
            ws.send_json({"type": "devices", "devices": [{"uuid": "u1", "name": "HP"}]})
            for _ in range(50):
                if hub.connected(1, "client-abc"):
                    break
                threading.Event().wait(0.02)
            t, box = run_request(hub, 1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=60)
            ws.receive_json()  # the escl message
        t.join(5)
        assert not t.is_alive()
        assert isinstance(box["error"], AgentOffline)
        assert not hub.connected(1, "client-abc")


def test_request_timeout():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client, client.websocket_connect("/ws/1/client-abc") as ws:
        ws.receive_json()
        for _ in range(50):
            if hub.connected(1, "client-abc"):
                break
            threading.Event().wait(0.02)
        t, box = run_request(hub, 1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=0.3)
        ws.receive_json()
        t.join(5)
        assert isinstance(box["error"], AgentTimeout)


def test_second_agent_replaces_first():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client:
        with client.websocket_connect("/ws/1/client-abc") as first:
            first.receive_json()
            first.send_json({"type": "devices", "devices": [{"uuid": "old", "name": "Old"}]})
            for _ in range(50):
                if hub.scanners(1, "client-abc"):
                    break
                threading.Event().wait(0.02)
            t, box = run_request(hub, 1, "client-abc", "old", "GET", "ScannerCapabilities", timeout=60)
            first.receive_json()
            with client.websocket_connect("/ws/1/client-abc") as second:
                second.receive_json()
                second.send_json({"type": "devices", "devices": [{"uuid": "new", "name": "New"}]})
                t.join(5)
                assert isinstance(box["error"], AgentOffline)
                for _ in range(50):
                    if hub.scanners(1, "client-abc") == [{"uuid": "new", "name": "New"}]:
                        break
                    threading.Event().wait(0.02)
                assert hub.scanners(1, "client-abc") == [{"uuid": "new", "name": "New"}]


def test_malformed_messages_keep_agent_connected():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client, client.websocket_connect("/ws/1/client-abc") as ws:
        ws.receive_json()
        ws.send_text("not json")
        ws.send_json([1, 2])
        ws.send_json({"type": "devices", "devices": [{"name": "no uuid"}]})
        ws.send_json({"type": "devices", "devices": [{"uuid": "u1", "name": "HP"}]})
        for _ in range(50):
            if hub.scanners(1, "client-abc"):
                break
            threading.Event().wait(0.02)
        assert hub.connected(1, "client-abc")
        t, box = run_request(hub, 1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=5)
        rid = ws.receive_json()["id"]
        ws.send_json({"type": "escl_response", "id": rid, "status": "bad"})
        ws.send_json({"type": "escl_response", "id": rid, "status": 200, "content_type": "text/xml"})
        ws.send_json({"type": "end", "id": rid})
        t.join(5)
        assert box["result"].status == 200


def test_request_after_disconnect_is_offline_fast():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client:
        with client.websocket_connect("/ws/1/client-abc") as ws:
            ws.receive_json()
        for _ in range(50):
            if not hub.connected(1, "client-abc"):
                break
            threading.Event().wait(0.02)
        with pytest.raises(AgentOffline):
            hub.request(1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=30)


def test_resume_token_is_refreshed_while_connected(monkeypatch):
    from app.services import agent_hub as agent_hub_module

    monkeypatch.setattr(agent_hub_module, "RESUME_REFRESH_SECONDS", 0.05)
    hub = AgentHub()
    with TestClient(make_app(hub)) as client, client.websocket_connect("/ws/1/client-abc") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert first["type"] == second["type"] == "welcome"
    assert first["resume_token"] != second["resume_token"]
    for token in (first["resume_token"], second["resume_token"]):
        assert hub.consume_token(token) == (1, "client-abc")
        assert hub.consume_token(token) is None


def test_request_discover():
    hub = AgentHub()
    assert hub.request_discover(1, "client-abc") is False
    with TestClient(make_app(hub)) as client, client.websocket_connect("/ws/1/client-abc") as ws:
        ws.receive_json()
        for _ in range(50):
            if hub.connected(1, "client-abc"):
                break
            threading.Event().wait(0.02)
        assert hub.request_discover(1, "client-abc") is True
        assert ws.receive_json() == {"type": "discover"}
