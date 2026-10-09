import io

import pytest
from PIL import Image

from app.services.agent_hub import AgentOffline, AgentTimeout, EsclResponse
from app.services.escl import EsclRemoteBackend, parse_agent_device
from app.services.scanner import ScannerBusy, ScannerError, ScannerOffline, ScannerTimeout
from tests.test_escl import CAPS

CLIENT = "0f8e2a4c-1111-4222-8333-444455556666"
DEVICE = f"agent:{CLIENT}:u1"


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), "white").save(buf, "JPEG")
    return buf.getvalue()


class FakeHub:
    def __init__(self, user_id=1, scanners=None):
        self.user_id = user_id
        self._scanners = scanners if scanners is not None else [{"uuid": "u1", "name": "HP Envy"}]
        self.calls = []
        self.next_document = [EsclResponse(200, "image/jpeg", {}, jpeg())]
        self.post_status = 201
        self.error = None

    def connected(self, user_id, client_id):
        return user_id == self.user_id and client_id == CLIENT

    def scanners(self, user_id, client_id):
        return list(self._scanners) if self.connected(user_id, client_id) else []

    def request(self, user_id, client_id, scanner_uuid, method, path, body=None, timeout=120.0):
        self.calls.append((method, path, body))
        if self.error:
            raise self.error
        if not self.connected(user_id, client_id):
            raise AgentOffline()
        if path == "ScannerCapabilities":
            return EsclResponse(200, "text/xml", {}, CAPS)
        if method == "POST" and path == "ScanJobs":
            return EsclResponse(self.post_status, "", {"Location": "ScanJobs/7"}, b"")
        if path == "ScanJobs/7/NextDocument":
            return self.next_document.pop(0)
        if path == "ScannerStatus":
            return EsclResponse(200, "text/xml", {}, b"<State>Processing</State>")
        return EsclResponse(404, "", {}, b"")


def test_parse_agent_device():
    assert parse_agent_device(DEVICE) == (CLIENT, "u1")
    assert parse_agent_device(f"agent:{CLIENT}:a:b") == (CLIENT, "a:b")
    with pytest.raises(ScannerOffline):
        parse_agent_device("agent:bad")


def test_devices_and_available():
    remote = EsclRemoteBackend(FakeHub(), user_id=1)
    assert remote.devices(CLIENT) == [{"id": DEVICE, "name": "HP Envy"}]
    assert remote.available(DEVICE)
    assert not remote.available(f"agent:{CLIENT}:gone")


def test_scan_runs_job_and_returns_png():
    hub = FakeHub()
    png = EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)
    assert png.startswith(b"\x89PNG")
    methods = [(m, p) for m, p, _ in hub.calls]
    assert methods == [("GET", "ScannerCapabilities"), ("POST", "ScanJobs"), ("GET", "ScanJobs/7/NextDocument")]
    assert "<scan:ColorMode>RGB24</scan:ColorMode>" in hub.calls[1][2]


def test_capabilities_are_cached():
    hub = FakeHub()
    remote = EsclRemoteBackend(hub, user_id=1)
    remote.scan(dpi=300, mode="Color", device=DEVICE)
    hub.next_document = [EsclResponse(200, "image/jpeg", {}, jpeg())]
    remote.preview(device=DEVICE)
    assert [p for _, p, _ in hub.calls].count("ScannerCapabilities") == 1
    assert "<scan:ColorMode>Grayscale8</scan:ColorMode>" in hub.calls[-2][2]


def test_next_document_503_is_retried():
    hub = FakeHub()
    hub.next_document = [
        EsclResponse(503, "", {}, b""),
        EsclResponse(503, "", {}, b""),
        EsclResponse(200, "image/jpeg", {}, jpeg()),
    ]
    sleeps = []
    png = EsclRemoteBackend(hub, user_id=1, sleep=sleeps.append).scan(dpi=300, mode="Color", device=DEVICE)
    assert png.startswith(b"\x89PNG")
    assert len(sleeps) == 2


def test_busy_scanner_on_post():
    hub = FakeHub()
    hub.post_status = 503
    with pytest.raises(ScannerBusy):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


def test_agent_errors_map_to_scanner_errors():
    hub = FakeHub()
    hub.error = AgentOffline()
    with pytest.raises(ScannerOffline):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)
    hub.error = AgentTimeout()
    with pytest.raises(ScannerTimeout):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


def test_other_users_device_is_offline():
    with pytest.raises(ScannerOffline):
        EsclRemoteBackend(FakeHub(user_id=2), user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


def test_malformed_capabilities_is_scanner_error():
    hub = FakeHub()
    hub.request = lambda *a, **k: EsclResponse(200, "text/xml", {}, b"<not xml")
    with pytest.raises(ScannerError):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


def test_unreadable_image_is_scanner_error():
    hub = FakeHub()
    hub.next_document = [EsclResponse(200, "image/jpeg", {}, b"garbage-not-an-image")]
    with pytest.raises(ScannerError, match="unreadable image"):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


def test_to_png_converts_cmyk():
    from app.services.escl import to_png

    buf = io.BytesIO()
    Image.new("CMYK", (10, 10)).save(buf, "JPEG")
    out = to_png(buf.getvalue())
    assert out.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(out)).mode == "RGB"


@pytest.mark.parametrize(
    "location", ["ScanJobs/7", "/eSCL/ScanJobs/7/", "http://192.168.1.5/eSCL/ScanJobs/7"]
)
def test_job_path_normalizes_location(location):
    from app.services.escl import job_path

    assert job_path(location) == "ScanJobs/7"


def test_job_path_rejects_bad_location():
    from app.services.escl import job_path

    with pytest.raises(ScannerError):
        job_path("http://host/other/7")


# --- API wiring ----------------------------------------------------------


@pytest.fixture
def fake_hub(client, user):
    from app.main import app as main_app
    from app.services.agent_hub import get_agent_hub

    hub = FakeHub(user_id=user.id)
    main_app.dependency_overrides[get_agent_hub] = lambda: hub
    yield hub
    main_app.dependency_overrides.pop(get_agent_hub, None)


def test_devices_lists_server_then_agent(auth_client, fake_scanner, fake_hub):
    data = auth_client.get("/api/scan/devices", params={"client_id": CLIENT}).json()
    assert [d["id"] for d in data["devices"]] == ["fake:0", DEVICE]
    assert data["default"] == "fake:0"
    assert data["agent_connected"] is True


def test_devices_without_client_id_is_server_only(auth_client, fake_scanner, fake_hub):
    data = auth_client.get("/api/scan/devices").json()
    assert [d["id"] for d in data["devices"]] == ["fake:0"]
    assert data["agent_connected"] is False


def test_page_scan_routes_agent_device(auth_client, fake_scanner, fake_hub, storage):
    sid = auth_client.post("/api/scan/sessions", json={"device": DEVICE}).json()["id"]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert resp.status_code == 201
    stored = storage.scan_session_dir(sid) / "page_001.png"
    assert stored.read_bytes().startswith(b"\x89PNG")
    assert fake_scanner.last_device is None  # the server scanner was not used


def test_status_for_agent_device(auth_client, fake_scanner, fake_hub):
    data = auth_client.get("/api/scan/status", params={"client_id": CLIENT, "device": DEVICE}).json()
    assert data == {"available": True, "busy": False}
    gone = auth_client.get("/api/scan/status", params={"client_id": CLIENT, "device": f"agent:{CLIENT}:x"}).json()
    assert gone["available"] is False


def test_preview_agent_device_offline_is_503(auth_client, fake_scanner, fake_hub):
    fake_hub.error = AgentOffline()
    resp = auth_client.post("/api/scan/preview", json={"device": DEVICE})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "scanner_offline"
