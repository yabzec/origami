from pathlib import Path

from fastapi import FastAPI

from app.api import auth, chat, documents, files, folders, scan, search, tags, uploads
from app.api.error_handlers import register_error_handlers
from app.api.spa import register_spa

app = FastAPI(title="Origami")
register_error_handlers(app)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(files.router)
app.include_router(folders.router)
app.include_router(scan.router)
app.include_router(search.router)
app.include_router(tags.router)
app.include_router(uploads.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (FRONTEND_DIST / "index.html").is_file():
    register_spa(app, FRONTEND_DIST)
