from fastapi import FastAPI

from app.api import auth, folders

app = FastAPI(title="Origami")
app.include_router(auth.router)
app.include_router(folders.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
