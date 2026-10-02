import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.api.deps import api_error, get_current_user_flexible
from app.api.documents import get_doc_or_404
from app.db import get_session
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/documents",
    tags=["files"],
    dependencies=[Depends(get_current_user_flexible)],
)

NO_CACHE = {"Cache-Control": "no-cache"}  # re-process may replace the file at the same path


@router.get("/{document_id}/file")
def document_file(
    document_id: uuid.UUID,
    download: bool = False,
    preview: bool = False,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    doc = get_doc_or_404(session, document_id)
    if preview and not download and doc.preview_path:
        preview_file = storage.abs_path(doc.preview_path)
        if preview_file.is_file():
            return FileResponse(
                preview_file,
                media_type="application/pdf",
                filename=f"{Path(doc.original_filename or preview_file.name).stem}.pdf",
                content_disposition_type="inline",
                headers=NO_CACHE,
            )
    if not doc.file_path:
        raise api_error(404, "no_file", "Document has no stored file")
    path = storage.abs_path(doc.file_path)
    if not path.is_file():
        raise api_error(404, "no_file", "Stored file is missing on disk")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # inline lets the <iframe> render PDFs; attachment only for the explicit Download button
    return FileResponse(
        path,
        media_type=media_type,
        filename=doc.original_filename or path.name,
        content_disposition_type="attachment" if download else "inline",
        headers=NO_CACHE,
    )
