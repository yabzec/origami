from fastapi import FastAPI

app = FastAPI(title="Origami")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
