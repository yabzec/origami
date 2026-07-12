from fastapi import FastAPI

from app.api import auth

app = FastAPI(title="Origami")
app.include_router(auth.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
