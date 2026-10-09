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
    assert auth_client.get("/api/ocr/languages").json() == {
        "languages": [],
        "default": "",
        "translation_languages": [],
        "translation_default": "it",
    }


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


def test_upload_and_scan_fallback_default_uses_installed_languages(
    auth_client, session, storage, fake_scanner, installed, monkeypatch
):
    from app.config import get_settings
    from app.models import Document

    installed(["eng"])
    monkeypatch.setenv("DEFAULT_OCR_LANGUAGES", "ita+eng")
    get_settings.cache_clear()
    try:
        resp = auth_client.post(
            "/api/documents/upload", files={"file": ("a.pdf", b"%PDF", "application/pdf")}
        )
        scan = auth_client.post("/api/scan/sessions", json={})
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 201
    assert session.get(Document, resp.json()["id"]).ocr_languages == "eng"
    assert scan.json()["ocr_languages"] == "eng"


def test_translation_languages_map_and_dedupe(installed):
    from app.api.ocr import translation_languages

    installed(["eng", "ita", "chi_sim", "chi_tra", "lat", "zzz"])
    assert translation_languages() == ["zh", "en", "it", "la"]  # sorted by name: Chinese, English, Italian, Latin


def test_translation_default_falls_back(installed, monkeypatch):
    from app.api.ocr import default_translation_language
    from app.config import get_settings

    installed(["eng", "deu"])
    monkeypatch.setenv("DEFAULT_TRANSLATION_LANGUAGE", "fr")
    get_settings.cache_clear()
    try:
        assert default_translation_language() == "en"  # first by name: English, German
        monkeypatch.setenv("DEFAULT_TRANSLATION_LANGUAGE", "en")
        get_settings.cache_clear()
        assert default_translation_language() == "en"
    finally:
        get_settings.cache_clear()


def test_languages_endpoint_lists_translation_targets(auth_client, installed):
    installed(["eng", "ita"])
    body = auth_client.get("/api/ocr/languages").json()
    assert body["translation_languages"] == [
        {"code": "en", "name": "English"},
        {"code": "it", "name": "Italian"},
    ]
    assert body["translation_default"] == "it"


def test_norwegian_maps_to_bokmal_code():
    from app.services.ocr_language_names import iso_language, iso_language_name

    assert iso_language("nor") == "nb"  # lingua reports Norwegian Bokmal as "nb"
    assert iso_language_name("nb") == "Norwegian"
