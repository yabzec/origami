from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user
from app.api.documents import serialize
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
    results = []
    for entry in grouped:
        doc = session.get(Document, entry["document_id"])
        results.append(
            {
                "document": serialize(session, doc),
                "score": entry["score"],
                "snippets": entry["snippets"],
            }
        )
    return {"mode": body.mode, "results": results}
