from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user
from app.api.documents import active_jobs_for, docs_with_content, serialize
from app.db import get_session
from app.models import Document
from app.services.search import SearchFilters, search

router = APIRouter(
    prefix="/api/search", tags=["search"], dependencies=[Depends(get_current_user)]
)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    filters: SearchFilters = SearchFilters()
    limit: int = Field(default=10, ge=1, le=50)


@router.post("")
def run_search(body: SearchRequest, session: Session = Depends(get_session)) -> dict:
    grouped = search(session, body.query, body.mode, body.filters, body.limit)
    docs = [session.get(Document, entry["document_id"]) for entry in grouped]
    ids = [doc.id for doc in docs]
    active_jobs = active_jobs_for(session, ids)
    with_content = docs_with_content(session, ids)
    results = []
    for entry, doc in zip(grouped, docs):
        results.append(
            {
                "document": serialize(session, doc, active_jobs, with_content),
                "score": entry["score"],
                "snippets": entry["snippets"],
            }
        )
    return {"mode": body.mode, "results": results}
