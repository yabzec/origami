import mimetypes
import uuid

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


@router.get("/{document_id}/file")
def document_file(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    doc = get_doc_or_404(session, document_id)
    if not doc.file_path:
        raise api_error(404, "no_file", "Document has no stored file")
    path = storage.abs_path(doc.file_path)
    if not path.is_file():
        raise api_error(404, "no_file", "Stored file is missing on disk")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=doc.original_filename or path.name)
