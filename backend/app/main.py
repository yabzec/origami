from fastapi import FastAPI

from app.api import auth, documents, folders, scan, search, tags, uploads
from app.api.error_handlers import register_error_handlers

app = FastAPI(title="Origami")
register_error_handlers(app)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(folders.router)
app.include_router(scan.router)
app.include_router(search.router)
app.include_router(tags.router)
app.include_router(uploads.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
