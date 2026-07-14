import json
import logging

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

log = logging.getLogger("origami.chat")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    def event_stream():
        # NOTE: stream_answer payload dicts must never contain a "type" key —
        # it would be clobbered by the event type merged in here.
        try:
            for event_type, payload in stream_answer(session, body.question):
                yield f"data: {json.dumps({'type': event_type, **payload})}\n\n"
        except Exception:
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'code': 'chat_failed', 'message': 'Answer generation failed'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
