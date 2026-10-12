from datetime import date

from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job, JobStatus
from app.services.study_metadata import cover_rows, properties_text, study_properties
from app.worker import pipeline


META = [
    {"key": "Patient", "value": "Mario Rossi"},
    {"key": "Modality", "value": "DX"},
    {"key": "Institution", "value": "Ospedale Test"},
    {"key": "Series", "value": "AP (2); Lateral (1)"},
]


def make_ready_doc(session, **kw):
    doc = Document(title=kw.pop("title", "Visita"), doc_type=DocType.pdf, status=DocStatus.ready, **kw)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_study_properties_lists_known_fields_in_order():
    fields = {"modality": "DX", "patient_name": "Mario Rossi", "sex": "", "institution": "Ospedale Test"}
    series = [{"description": "AP", "images": 2}, {"description": "Lateral", "images": 1}]
    assert study_properties(fields, series) == [
        {"key": "Patient", "value": "Mario Rossi"},
        {"key": "Modality", "value": "DX"},
        {"key": "Institution", "value": "Ospedale Test"},
        {"key": "Series", "value": "AP (2); Lateral (1)"},
    ]
    assert study_properties({}, []) == []


def test_properties_text_and_cover_rows():
    props = [{"key": "Patient", "value": "Mario Rossi"}, {"key": "Colore", "value": "blu"}]
    assert properties_text(props) == "Patient: Mario Rossi\nColore: blu"
    assert properties_text([]) == ""
    assert cover_rows(props, date(2026, 8, 28)) == [
        ("Study date", "2026-08-28"), ("Patient", "Mario Rossi"), ("Colore", "blu"),
    ]


def test_patch_notes_queues_metadata_refresh(auth_client, session, storage):
    doc = make_ready_doc(session)
    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.metadata, content="Visita"))
    session.commit()

    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"notes": "Controllo a 3 mesi"})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Controllo a 3 mesi"
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).all() == []
    job = session.exec(select(Job).where(Job.type == "refresh_metadata")).one()
    assert job.payload == {"document_id": str(doc.id)}


def test_patch_same_metadata_values_queues_nothing(auth_client, session, storage):
    doc = make_ready_doc(session, notes="Controllo")
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"title": doc.title, "notes": "Controllo"})
    assert resp.status_code == 200
    assert session.exec(select(Job).where(Job.type == "refresh_metadata")).all() == []


def test_patch_without_metadata_fields_queues_nothing(auth_client, session, storage):
    doc = make_ready_doc(session)
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2026-01-02"})
    assert resp.status_code == 200
    assert session.exec(select(Job).where(Job.type == "refresh_metadata")).all() == []


def test_refresh_metadata_rebuilds_chunk_with_notes_and_study_data(session, llm_stub):
    doc = make_ready_doc(session, notes="Controllo a 3 mesi", properties=META)
    pipeline.refresh_metadata(session, {"document_id": str(doc.id)})
    chunk = session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).one()
    assert chunk.content.startswith("Visita\n\nControllo a 3 mesi\n\nPatient: Mario Rossi")
    assert chunk.embedding is not None


def test_refresh_metadata_waits_while_processing(session, llm_stub):
    doc = make_ready_doc(session)
    doc.status = DocStatus.processing
    session.commit()
    pipeline.refresh_metadata(session, {"document_id": str(doc.id)})
    assert session.exec(select(Chunk)).all() == []
    job = session.exec(select(Job).where(Job.type == "refresh_metadata")).one()
    assert job.status == JobStatus.queued


def test_upload_accepts_notes(auth_client, session, storage):
    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.pdf", b"%PDF-1.7 fake", "application/pdf")},
        data={"notes": "Da portare alla visita"},
    )
    assert resp.status_code == 201
    assert resp.json()["notes"] == "Da portare alla visita"
