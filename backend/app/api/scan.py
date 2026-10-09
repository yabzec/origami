from datetime import date

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.agent import CLIENT_ID_RE
from app.api.deps import api_error, get_current_user
from app.api.documents import serialize
from app.api.ocr import check_ocr_languages, check_translation_language, default_ocr_languages
from app.api.uploads import create_pending_document
from app.db import get_session
from app.models import User, DocType, ScanPage, ScanSession, ScanSessionStatus
from app.services.agent_hub import AgentHub, get_agent_hub
from app.services.escl import EsclRemoteBackend
from app.services.jobs import enqueue
from app.services.scanner import CompositeBackend, ScannerBackend, device_busy, get_scanner, preview_locked, scan_locked
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/scan", tags=["scan"], dependencies=[Depends(get_current_user)]
)


class SessionCreate(BaseModel):
    ocr_languages: str | None = None
    ocr_enabled: bool = True
    device: str | None = None


class PageScanRequest(BaseModel):
    dpi: int = 300
    mode: str = "Color"
    device: str | None = None


class PreviewRequest(BaseModel):
    device: str | None = None


class ReorderRequest(BaseModel):
    page_ids: list[int]


class CompileRequest(BaseModel):
    title: str
    folder_id: int | None = None
    tag_ids: list[int] = []
    description: str = ""
    document_date: date | None = None
    ocr_languages: str | None = None
    ocr_enabled: bool | None = None
    translation_language: str | None = None
    summary_enabled: bool = True
    translation_enabled: bool = False


def get_session_or_404(db: Session, session_id: int) -> ScanSession:
    scan_session = db.get(ScanSession, session_id)
    if scan_session is None:
        raise api_error(404, "not_found", f"Scan session {session_id} not found")
    return scan_session


def session_pages(db: Session, session_id: int) -> list[ScanPage]:
    return list(
        db.exec(
            select(ScanPage)
            .where(ScanPage.session_id == session_id)
            .order_by(ScanPage.page_number)
        )
    )


def get_scan_backend(
    client_id: str | None = None,
    user: User = Depends(get_current_user),
    local: ScannerBackend = Depends(get_scanner),
    hub: AgentHub = Depends(get_agent_hub),
) -> CompositeBackend:
    if client_id is not None and not CLIENT_ID_RE.match(client_id):
        client_id = None
    return CompositeBackend(local, EsclRemoteBackend(hub, user.id), client_id)


@router.get("/status")
def scan_status(
    device: str | None = None, backend: CompositeBackend = Depends(get_scan_backend)
) -> dict:
    return {"available": backend.available(device), "busy": device_busy(device)}


@router.get("/devices")
def scan_devices(
    client_id: str | None = None,
    backend: CompositeBackend = Depends(get_scan_backend),
    user: User = Depends(get_current_user),
    hub: AgentHub = Depends(get_agent_hub),
) -> dict:
    devices = backend.list_devices()
    connected = bool(client_id) and hub.connected(user.id, client_id)
    return {"devices": devices, "default": devices[0]["id"] if devices else None, "agent_connected": connected}


@router.post("/preview")
def scan_preview(
    body: PreviewRequest, backend: CompositeBackend = Depends(get_scan_backend)
) -> Response:
    png = preview_locked(backend, device=body.device)
    return Response(content=png, media_type="image/png")


@router.post("/sessions", status_code=201)
def create_session(
    body: SessionCreate, db: Session = Depends(get_session)
) -> ScanSession:
    if body.ocr_enabled:
        check_ocr_languages(body.ocr_languages)
    scan_session = ScanSession(
        ocr_languages=body.ocr_languages or default_ocr_languages(),
        ocr_enabled=body.ocr_enabled,
        device=body.device,
    )
    db.add(scan_session)
    db.commit()
    db.refresh(scan_session)
    return scan_session


@router.post("/sessions/{session_id}/pages", status_code=201)
def scan_page(
    session_id: int,
    body: PageScanRequest,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    backend: CompositeBackend = Depends(get_scan_backend),
) -> dict:
    scan_session = get_session_or_404(db, session_id)
    if scan_session.status != ScanSessionStatus.active:
        raise api_error(409, "session_not_active", "Scan session is not active")

    png = scan_locked(backend, dpi=body.dpi, mode=body.mode, device=body.device or scan_session.device)

    number = len(session_pages(db, session_id)) + 1
    filename = f"page_{number:03d}.png"
    (storage.scan_session_dir(session_id) / filename).write_bytes(png)
    page = ScanPage(
        session_id=session_id,
        page_number=number,
        image_path=f"scan_sessions/{session_id}/{filename}",
    )
    db.add(page)
    db.commit()
    db.refresh(page)
    return {
        "id": page.id,
        "page_number": page.page_number,
        "preview_url": f"/api/scan/pages/{page.id}/preview",
    }


@router.get("/pages/{page_id}/preview")
def page_preview(
    page_id: int,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    page = db.get(ScanPage, page_id)
    if page is None:
        raise api_error(404, "not_found", f"Scan page {page_id} not found")
    return FileResponse(storage.tmp_abs(page.image_path), media_type="image/png")


@router.delete("/pages/{page_id}", status_code=204)
def delete_page(
    page_id: int,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    page = db.get(ScanPage, page_id)
    if page is None:
        raise api_error(404, "not_found", f"Scan page {page_id} not found")
    storage.tmp_abs(page.image_path).unlink(missing_ok=True)
    session_id, removed_number = page.session_id, page.page_number
    db.delete(page)
    db.commit()
    for later in session_pages(db, session_id):
        if later.page_number > removed_number:
            later.page_number -= 1
            db.add(later)
    db.commit()


@router.post("/sessions/{session_id}/reorder")
def reorder_pages(
    session_id: int, body: ReorderRequest, db: Session = Depends(get_session)
) -> dict:
    get_session_or_404(db, session_id)
    pages = session_pages(db, session_id)
    if sorted(body.page_ids) != sorted(p.id for p in pages):
        raise api_error(422, "invalid_order", "page_ids must be exactly the session's pages")
    by_id = {p.id: p for p in pages}
    # two-phase renumber to dodge any (session, page_number) collisions mid-update
    for offset, page_id in enumerate(body.page_ids):
        by_id[page_id].page_number = 1000 + offset
        db.add(by_id[page_id])
    db.commit()
    for offset, page_id in enumerate(body.page_ids, start=1):
        by_id[page_id].page_number = offset
        db.add(by_id[page_id])
    db.commit()
    return {"pages": [{"id": p.id, "page_number": p.page_number} for p in session_pages(db, session_id)]}


@router.delete("/sessions/{session_id}", status_code=204)
def cancel_session(
    session_id: int,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    scan_session = get_session_or_404(db, session_id)
    if scan_session.status != ScanSessionStatus.active:
        raise api_error(409, "session_not_active", "Scan session is not active")
    for page in session_pages(db, session_id):
        db.delete(page)
    scan_session.status = ScanSessionStatus.cancelled
    db.commit()
    storage.remove_scan_session_dir(session_id)


@router.post("/sessions/{session_id}/compile", status_code=201)
def compile_session(
    session_id: int, body: CompileRequest, db: Session = Depends(get_session)
) -> dict:
    scan_session = get_session_or_404(db, session_id)
    ocr_enabled = scan_session.ocr_enabled if body.ocr_enabled is None else body.ocr_enabled
    if ocr_enabled:
        check_ocr_languages(body.ocr_languages)
    check_translation_language(body.translation_language)
    if scan_session.status != ScanSessionStatus.active:
        raise api_error(409, "session_not_active", "Scan session is not active")
    if not session_pages(db, session_id):
        raise api_error(422, "no_pages", "Scan session has no pages to compile")

    doc = create_pending_document(
        db,
        title=body.title,
        description=body.description,
        document_date=body.document_date,
        doc_type=DocType.scan,
        ocr_languages=body.ocr_languages or scan_session.ocr_languages,
        ocr_enabled=ocr_enabled,
        summary_enabled=body.summary_enabled,
        translation_enabled=body.translation_enabled,
        translation_language=body.translation_language,
        folder_id=body.folder_id,
        tag_ids=body.tag_ids,
        original_filename=None,
    )
    scan_session.status = ScanSessionStatus.compiling
    db.commit()
    enqueue(
        db,
        "process_document",
        {"document_id": str(doc.id), "scan_session_id": session_id},
    )
    db.refresh(doc)
    return serialize(db, doc)
