from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, auth, bulk, chat, disc_imports, documents, files, folders, ocr, scan, search, tags, uploads
from app.api.error_handlers import register_error_handlers
from app.api.spa import register_spa
from app.config import get_settings

def embedding_startup_guard() -> None:
    """Refuse to serve when stored vectors don't match EMBEDDING_MODEL (replaced in tests)."""
    from app.db import engine
    from app.services.embedding_check import startup_guard

    startup_guard(engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    embedding_startup_guard()
    yield


app = FastAPI(title="Origami", lifespan=lifespan)

_origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)
app.include_router(agent.router)
app.include_router(auth.router)
app.include_router(bulk.router)
app.include_router(chat.router)
app.include_router(disc_imports.router)
app.include_router(documents.router)
app.include_router(files.router)
app.include_router(folders.router)
app.include_router(ocr.router)
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
