"""Import an X-ray disc: stage its files, preview the studies, then create one document per study."""

import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user, get_current_user_flexible
from app.api.documents import serialize
from app.api.ocr import check_ocr_languages, check_translation_language, default_ocr_languages
from app.api.uploads import create_pending_document
from app.db import get_session
from app.models import DiscImport, DiscImportStatus, DocType, Folder, Job, Tag, User
from app.services.dicom_parse import build_preview
from app.services.dicom_render import thumbnail_jpeg
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/api/disc-imports", tags=["disc-imports"])

IMPORT_MAX_AGE = timedelta(hours=24)
THUMBS_DIR = ".thumbs"
EMPTY_PREVIEW = {"no_study": True, "studies": [], "reports": [], "warnings": []}


class StudyCommit(BaseModel):
    study_key: str
    title: str = Field(min_length=1)
    document_date: date | None = None
    notes: str = ""
    folder_id: int | None = None
    tag_ids: list[int] = []
    image_keys: list[str] = Field(min_length=1)
    report_keys: list[str] = []

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value


class CommitRequest(BaseModel):
    studies: list[StudyCommit] = Field(min_length=1)
    ocr_languages: str | None = None
    ocr_enabled: bool = False
    summary_enabled: bool = True
    translation_enabled: bool = False
    translation_language: str | None = None


def staged_path(root: Path, rel: str) -> Path:
    """Target inside the staging dir; ValueError for absolute, parent, drive or dot-prefixed paths."""
    rel = rel.replace("\\", "/")
    parts = PurePosixPath(rel).parts
    if (
        not parts
        or rel.startswith("/")
        or ":" in parts[0]
        or any(p in ("", ".", "..") or p.startswith(".") for p in parts)
    ):
        raise ValueError(f"Invalid path {rel!r}")
    return root.joinpath(*parts)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_import(session: Session, import_id: uuid.UUID, user: User, *, staging: bool = True) -> DiscImport:
    imp = session.get(DiscImport, import_id)
    if imp is None or imp.user_id != user.id:
        raise api_error(404, "not_found", "Import not found")
    if _aware(imp.created_at) < datetime.now(timezone.utc) - IMPORT_MAX_AGE:
        raise api_error(410, "import_expired", "This import has expired; select the disc again")
    if staging and imp.status != DiscImportStatus.staging:
        raise api_error(409, "import_closed", "This import is already committed or cancelled")
    return imp


def preview_response(session: Session, imp: DiscImport) -> dict:
    preview = imp.preview or EMPTY_PREVIEW
    tags = {t.name.casefold(): t.id for t in session.exec(select(Tag))}
    studies = [
        {**s, "patient_tag_id": tags.get((s.get("patient_tag") or "").casefold())} for s in preview["studies"]
    ]
    return {**preview, "id": str(imp.id), "studies": studies}


def _find_image(imp: DiscImport, key: str) -> dict:
    for study in (imp.preview or EMPTY_PREVIEW)["studies"]:
        for image in study["images"]:
            if image["key"] == key:
                return image
    raise api_error(404, "not_found", "Image not found")


def _find_report(imp: DiscImport, key: str) -> dict:
    for report in (imp.preview or EMPTY_PREVIEW)["reports"]:
        if report["key"] == key:
            return report
    raise api_error(404, "not_found", "Report not found")


def _cancel(session: Session, storage: Storage, imp: DiscImport) -> None:
    imp.status = DiscImportStatus.cancelled
    session.commit()
    storage.remove_disc_import_dir(imp.id)


@router.post("", status_code=201)
def create_import(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    imp = DiscImport(user_id=user.id)
    session.add(imp)
    session.commit()
    storage.disc_import_dir(imp.id)
    return {"id": str(imp.id)}


@router.post("/{import_id}/files")
def add_files(
    import_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    imp = get_import(session, import_id, user)
    if len(files) != len(paths):
        raise api_error(422, "validation_error", "Send one path per file")
    root = storage.disc_import_dir(imp.id)
    try:
        targets = [staged_path(root, rel) for rel in paths]
    except ValueError as exc:
        _cancel(session, storage, imp)
        raise api_error(400, "invalid_path", str(exc))
    for upload, target in zip(files, targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
    imp.preview = build_preview(root)
    session.commit()
    return preview_response(session, imp)


@router.get("/{import_id}")
def get_preview(
    import_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    return preview_response(session, get_import(session, import_id, user, staging=False))


@router.get("/{import_id}/images/{key}/thumb")
def image_thumb(
    import_id: uuid.UUID,
    key: str,
    user: User = Depends(get_current_user_flexible),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    imp = get_import(session, import_id, user, staging=False)
    image = _find_image(imp, key)
    root = storage.disc_import_path(imp.id)
    cached = root / THUMBS_DIR / f"{key}.jpg"
    if not cached.is_file():
        try:
            data = thumbnail_jpeg(root / image["path"], image["kind"])
        except Exception:
            raise api_error(422, "unreadable_image", "This image cannot be decoded")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return FileResponse(cached, media_type="image/jpeg")


@router.get("/{import_id}/reports/{key}/file")
def report_file(
    import_id: uuid.UUID,
    key: str,
    user: User = Depends(get_current_user_flexible),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    imp = get_import(session, import_id, user, staging=False)
    report = _find_report(imp, key)
    path = storage.disc_import_path(imp.id) / report["path"]
    if not path.is_file():
        raise api_error(404, "not_found", "Report not found")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=report["filename"],
        content_disposition_type="inline",
    )


@router.delete("/{import_id}", status_code=204)
def cancel_import(
    import_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    _cancel(session, storage, get_import(session, import_id, user))


@router.post("/{import_id}/commit")
def commit_import(
    import_id: uuid.UUID,
    body: CommitRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    imp = get_import(session, import_id, user)
    # lock the row for the whole commit: a double submit waits, then sees the import closed
    session.refresh(imp, with_for_update=True)
    if imp.status != DiscImportStatus.staging:
        raise api_error(409, "import_closed", "This import is already committed or cancelled")
    if body.ocr_enabled:
        check_ocr_languages(body.ocr_languages)
    check_translation_language(body.translation_language)
    preview = imp.preview or EMPTY_PREVIEW
    studies = {s["key"]: s for s in preview["studies"]}
    reports = {r["key"]: r for r in preview["reports"]}

    # validate everything first: a bad second study must not leave the first one created
    for item in body.studies:
        study = studies.get(item.study_key)
        if study is None:
            raise api_error(422, "validation_error", f"Unknown study {item.study_key!r}")
        readable = {i["key"] for i in study["images"] if i["readable"]}
        if not set(item.image_keys) <= readable:
            raise api_error(422, "validation_error", "Unknown or unreadable image selected")
        if not set(item.report_keys) <= reports.keys():
            raise api_error(422, "validation_error", "Unknown report selected")
        if item.folder_id is not None and session.get(Folder, item.folder_id) is None:
            raise api_error(404, "not_found", "Folder not found")
        for tag_id in item.tag_ids:
            if session.get(Tag, tag_id) is None:
                raise api_error(404, "not_found", f"Tag {tag_id} not found")

    documents = []
    for item in body.studies:
        study = studies[item.study_key]
        images = {i["key"]: i for i in study["images"]}
        doc = create_pending_document(
            session,
            title=item.title,
            doc_type=DocType.pdf,
            ocr_languages=body.ocr_languages or default_ocr_languages(),
            ocr_enabled=body.ocr_enabled,
            summary_enabled=body.summary_enabled,
            translation_enabled=body.translation_enabled,
            translation_language=body.translation_language,
            folder_id=item.folder_id,
            tag_ids=item.tag_ids,
            original_filename=None,
            description="" if body.summary_enabled else study["description_fallback"],
            document_date=item.document_date,
            notes=item.notes,
            extra_metadata=study["extra_metadata"],
            commit=False,
        )
        payload = {
            "document_id": str(doc.id),
            "disc_import_id": str(imp.id),
            "images": [
                {"path": images[k]["path"], "kind": images[k]["kind"], "label": images[k]["label"]}
                for k in item.image_keys
            ],
            "reports": [{"path": reports[k]["path"]} for k in item.report_keys],
        }
        session.add(Job(type="process_document", payload=payload))
        documents.append(doc)
    # all jobs and the committed status land together: the worker never sees a partial import
    imp.status = DiscImportStatus.committed
    session.commit()
    return {"documents": [serialize(session, d) for d in documents]}
