import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlmodel import select

from app.models import DiscImport, DiscImportStatus, Document, Job, JobStatus, Tag, User
from app.services.auth import create_access_token, hash_password
from tests.helpers import make_dicom, make_text_pdf

REPORT = ["Referto: frattura saldata della tibia destra con callo osseo, nessuna mobilizzazione dei mezzi."]


def disc_files(tmp_path):
    """(path inside the disc, bytes) of a small standard disc."""
    a = make_dicom(tmp_path / "src/a", series_number=1).read_bytes()
    b = make_dicom(tmp_path / "src/b", series_number=2, SeriesDescription="Lateral").read_bytes()
    report = make_text_pdf(tmp_path / "src/r.pdf", REPORT).read_bytes()
    return [("DICOM/1/a", a), ("DICOM/1/b", b), ("REPORT/r.pdf", report), ("README.TXT", b"Sesso: M\n")]


def create(client) -> str:
    resp = client.post("/api/disc-imports")
    assert resp.status_code == 201
    return resp.json()["id"]


def send(client, import_id, files):
    return client.post(
        f"/api/disc-imports/{import_id}/files",
        files=[("files", (path.rsplit("/", 1)[-1], data, "application/octet-stream")) for path, data in files],
        data={"paths": [path for path, _ in files]},
    )


def staged(client, tmp_path):
    import_id = create(client)
    resp = send(client, import_id, disc_files(tmp_path))
    assert resp.status_code == 200
    return import_id, resp.json()


def test_stage_files_returns_preview(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    assert preview["id"] == import_id
    [study] = preview["studies"]
    assert study["patient_tag"] == "Mario Rossi"
    assert study["patient_tag_id"] is None
    assert len(study["images"]) == 2
    assert preview["reports"][0]["preselected"] is True
    assert storage.disc_import_path(import_id).joinpath("DICOM/1/a").is_file()
    assert auth_client.get(f"/api/disc-imports/{import_id}").json()["studies"][0]["key"] == study["key"]


def test_preview_finds_existing_patient_tag(auth_client, session, storage, tmp_path):
    tag = Tag(name="mario rossi")
    session.add(tag)
    session.commit()
    _, preview = staged(auth_client, tmp_path)
    assert preview["studies"][0]["patient_tag_id"] == tag.id


def test_files_in_several_batches_accumulate(auth_client, session, storage, tmp_path):
    import_id = create(auth_client)
    files = disc_files(tmp_path)
    send(auth_client, import_id, files[:1])
    preview = send(auth_client, import_id, files[1:]).json()
    assert len(preview["studies"][0]["images"]) == 2


def test_files_rejects_unsafe_paths(auth_client, session, storage, tmp_path):
    for bad in ("../escape", "/etc/x", "C:/x", ".thumbs/x", "a/../../b", ""):
        import_id = create(auth_client)
        resp = send(auth_client, import_id, [(bad, b"x")])
        if bad == "":
            # python-multipart drops the empty form value, so the files/paths count check answers
            assert resp.status_code in (400, 422)
            continue
        assert resp.status_code == 400, bad
        assert resp.json()["error"]["code"] == "invalid_path"
        assert not storage.disc_import_path(import_id).exists()
    assert not (storage.tmp_root / "escape").exists()


def test_files_and_paths_must_match(auth_client, session, storage):
    import_id = create(auth_client)
    resp = auth_client.post(
        f"/api/disc-imports/{import_id}/files",
        files=[("files", ("a", b"x", "application/octet-stream"))],
        data={"paths": ["a", "b"]},
    )
    assert resp.status_code == 422


def test_thumbnail_and_report_preview_accept_token(client, user, session, storage, tmp_path):
    from app.services.auth import create_access_token as token_for

    client.headers["Authorization"] = f"Bearer {token_for(user.id)}"
    import_id, preview = staged(client, tmp_path)
    del client.headers["Authorization"]
    token = token_for(user.id)
    image_key = preview["studies"][0]["images"][0]["key"]
    resp = client.get(f"/api/disc-imports/{import_id}/images/{image_key}/thumb?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(resp.content)).size) <= 256
    report_key = preview["reports"][0]["key"]
    resp = client.get(f"/api/disc-imports/{import_id}/reports/{report_key}/file?token={token}")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert client.get(f"/api/disc-imports/{import_id}/images/nope/thumb?token={token}").status_code == 404


def test_other_users_import_is_404(auth_client, session, storage):
    other = User(username="other", password_hash=hash_password("x"))
    session.add(other)
    session.commit()
    imp = DiscImport(user_id=other.id)
    session.add(imp)
    session.commit()
    assert auth_client.get(f"/api/disc-imports/{imp.id}").status_code == 404


def test_expired_import_is_410(auth_client, session, storage, user):
    imp = DiscImport(user_id=user.id, created_at=datetime.now(timezone.utc) - timedelta(hours=25))
    session.add(imp)
    session.commit()
    resp = auth_client.get(f"/api/disc-imports/{imp.id}")
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "import_expired"


def test_cancel_removes_staging(auth_client, session, storage, tmp_path):
    import_id, _ = staged(auth_client, tmp_path)
    assert auth_client.delete(f"/api/disc-imports/{import_id}").status_code == 204
    assert not storage.disc_import_path(import_id).exists()
    assert session.get(DiscImport, import_id).status == DiscImportStatus.cancelled


def commit_body(preview, **over):
    study = preview["studies"][0]
    body = {
        "studies": [{
            "study_key": study["key"],
            "title": "Rx gamba destra",
            "document_date": "2026-08-28",
            "notes": "Controllo",
            "folder_id": None,
            "tag_ids": [],
            "image_keys": [i["key"] for i in study["images"]],
            "report_keys": [preview["reports"][0]["key"]],
        }],
        "ocr_enabled": False,
        "summary_enabled": True,
        "translation_enabled": False,
    }
    body.update(over)
    return body


def test_commit_creates_documents_and_jobs(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    resp = auth_client.post(f"/api/disc-imports/{import_id}/commit", json=commit_body(preview))
    assert resp.status_code == 200
    [doc_json] = resp.json()["documents"]
    assert doc_json["title"] == "Rx gamba destra"
    assert doc_json["notes"] == "Controllo"
    assert doc_json["doc_type"] == "pdf"
    assert doc_json["ocr_enabled"] is False and doc_json["summary_enabled"] is True
    assert doc_json["extra_metadata"]["patient_name"] == "Mario Rossi"
    assert doc_json["description"] == ""  # the AI summary fills it
    job = session.exec(select(Job)).one()
    assert job.payload["disc_import_id"] == import_id
    assert job.payload["images"][0] == {"path": "DICOM/1/a", "kind": "dicom", "label": "AP"}
    assert job.payload["reports"] == [{"path": "REPORT/r.pdf"}]
    assert session.get(DiscImport, import_id).status == DiscImportStatus.committed
    assert auth_client.post(f"/api/disc-imports/{import_id}/commit", json=commit_body(preview)).status_code == 409


def test_commit_without_summary_fills_description(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    resp = auth_client.post(
        f"/api/disc-imports/{import_id}/commit", json=commit_body(preview, summary_enabled=False)
    )
    assert resp.json()["documents"][0]["description"] == "Rx gamba (tibia e perone) Destro/a · Ospedale Test"


def test_commit_rejects_unknown_keys_before_creating_anything(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    body = commit_body(preview)
    body["studies"][0]["image_keys"] = ["nope"]
    resp = auth_client.post(f"/api/disc-imports/{import_id}/commit", json=body)
    assert resp.status_code == 422
    assert session.exec(select(Document)).all() == []


def test_commit_rejects_unknown_folder(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    body = commit_body(preview)
    body["studies"][0]["folder_id"] = 999
    assert auth_client.post(f"/api/disc-imports/{import_id}/commit", json=body).status_code == 404
    assert session.exec(select(Document)).all() == []


def two_study_disc(tmp_path):
    files = disc_files(tmp_path)
    c = make_dicom(tmp_path / "src/c", study_uid="1.2.826.0.1.2").read_bytes()
    return files + [("DICOM/2/c", c)]


def test_commit_two_studies_queues_all_jobs_together(auth_client, session, storage, tmp_path):
    import_id = create(auth_client)
    resp = send(auth_client, import_id, two_study_disc(tmp_path))
    preview = resp.json()
    assert len(preview["studies"]) == 2
    body = commit_body(preview)
    second = preview["studies"][1]
    body["studies"].append({
        "study_key": second["key"], "title": "Secondo", "image_keys": [i["key"] for i in second["images"]],
    })
    resp = auth_client.post(f"/api/disc-imports/{import_id}/commit", json=body)
    assert resp.status_code == 200
    jobs = session.exec(select(Job)).all()
    assert len(jobs) == 2 and all(j.status == JobStatus.queued for j in jobs)
    assert session.get(DiscImport, import_id).status == DiscImportStatus.committed
    assert auth_client.post(f"/api/disc-imports/{import_id}/commit", json=body).status_code == 409


def test_commit_rejects_blank_title(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    body = commit_body(preview)
    body["studies"][0]["title"] = "   "
    resp = auth_client.post(f"/api/disc-imports/{import_id}/commit", json=body)
    assert resp.status_code == 422
    assert session.exec(select(Document)).all() == []


def test_report_file_missing_is_404(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    storage.disc_import_path(import_id).joinpath("REPORT/r.pdf").unlink()
    key = preview["reports"][0]["key"]
    resp = auth_client.get(f"/api/disc-imports/{import_id}/reports/{key}/file")
    assert resp.status_code == 404
