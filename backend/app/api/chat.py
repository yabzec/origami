import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user
from app.db import get_session
from app.services.rag import stream_answer

router = APIRouter(
    prefix="/api/chat", tags=["chat"], dependencies=[Depends(get_current_user)]
)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    def event_stream():
        for event_type, payload in stream_answer(session, body.question):
            yield f"data: {json.dumps({'type': event_type, **payload})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
