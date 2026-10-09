import pytest

from app.services import ocr


@pytest.fixture
def installed(monkeypatch):
    """Pretend Tesseract has exactly these languages installed."""
    def apply(codes):
        monkeypatch.setattr(ocr.pytesseract, "get_languages", lambda config="": list(codes))
        ocr.reset_language_cache()
    yield apply
    ocr.reset_language_cache()


def test_available_languages_drops_osd_and_equ(installed):
    installed(["eng", "osd", "ita", "equ", "chi_sim"])
    assert ocr.available_languages() == ["chi_sim", "eng", "ita"]


def test_available_languages_is_cached(installed, monkeypatch):
    installed(["eng"])
    assert ocr.available_languages() == ["eng"]
    monkeypatch.setattr(ocr.pytesseract, "get_languages", lambda config="": ["deu"])
    assert ocr.available_languages() == ["eng"]  # cached for 5 minutes
    ocr.reset_language_cache()
    assert ocr.available_languages() == ["deu"]


def test_unknown_languages(installed):
    installed(["eng", "ita"])
    assert ocr.unknown_languages("ita+eng") == []
    assert ocr.unknown_languages("ita+xyz") == ["xyz"]
    assert ocr.unknown_languages("") == [""]


def test_languages_endpoint(auth_client, installed, monkeypatch):
    installed(["eng", "ita", "zzz"])
    monkeypatch.setenv("DEFAULT_OCR_LANGUAGES", "ita+deu+eng")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        body = auth_client.get("/api/ocr/languages").json()
    finally:
        get_settings.cache_clear()
    assert body["languages"] == [
        {"code": "eng", "name": "English"},
        {"code": "ita", "name": "Italian"},
        {"code": "zzz", "name": "zzz"},
    ]
    assert body["default"] == "ita+eng"  # deu is not installed


def test_languages_endpoint_default_falls_back_to_first_installed(auth_client, installed, monkeypatch):
    installed(["fra"])
    from app.config import get_settings
    monkeypatch.setenv("DEFAULT_OCR_LANGUAGES", "ita+eng")
    get_settings.cache_clear()
    try:
        body = auth_client.get("/api/ocr/languages").json()
    finally:
        get_settings.cache_clear()
    assert body["default"] == "fra"


def test_languages_endpoint_nothing_installed(auth_client, installed):
    installed([])
    assert auth_client.get("/api/ocr/languages").json() == {"languages": [], "default": ""}


def _assert_unknown(resp, code="xyz"):
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_ocr_language"
    assert resp.json()["error"]["message"] == f"Unknown OCR language: {code}"


def test_upload_rejects_unknown_language(auth_client, storage, installed):
    installed(["eng"])
    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
        data={"ocr_languages": "eng+xyz"},
    )
    _assert_unknown(resp)


def test_scan_session_and_compile_reject_unknown_language(auth_client, fake_scanner, storage, installed):
    installed(["eng"])
    _assert_unknown(auth_client.post("/api/scan/sessions", json={"ocr_languages": "xyz"}))
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "eng"}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    _assert_unknown(
        auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "T", "ocr_languages": "xyz"})
    )


def test_reprocess_rejects_uninstalled_stored_language(auth_client, session, installed):
    from tests.helpers import seed_document

    installed(["eng"])
    doc = seed_document(session, "Doc", [{"content": "x", "page_number": 1}], doc_type="pdf")
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"})
    _assert_unknown(resp, "ita")


def test_languages_are_not_checked_when_ocr_is_disabled(auth_client, session, storage, fake_scanner, installed):
    from tests.helpers import seed_document

    installed(["eng"])
    doc = seed_document(session, "Doc", [{"content": "x", "page_number": 1}], doc_type="pdf")
    resp = auth_client.post(
        f"/api/documents/{doc.id}/reprocess", json={"ocr_enabled": False, "ocr_languages": "xyz"}
    )
    assert resp.status_code == 200
    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
        data={"ocr_languages": "xyz", "ocr_enabled": "false"},
    )
    assert resp.status_code == 201
    created = auth_client.post("/api/scan/sessions", json={"ocr_languages": "xyz", "ocr_enabled": False})
    assert created.status_code == 201
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "eng"}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    resp = auth_client.post(
        f"/api/scan/sessions/{sid}/compile",
        json={"title": "T", "ocr_languages": "xyz", "ocr_enabled": False},
    )
    assert resp.status_code == 201
