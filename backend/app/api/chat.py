import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from app.api.deps import get_current_user
from app.db import get_session
from app.services.rag import stream_chat

router = APIRouter(
    prefix="/api/chat", tags=["chat"], dependencies=[Depends(get_current_user)]
)

log = logging.getLogger("origami.chat")


MAX_MESSAGES = 40


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    pinned_ids: list[uuid.UUID] = []
    excluded_ids: list[uuid.UUID] = []

    @model_validator(mode="after")
    def last_message_from_user(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return self


@router.post("")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    messages = [m.model_dump() for m in body.messages]

    def event_stream():
        # NOTE: stream_chat payload dicts must never contain a "type" key —
        # it would be clobbered by the event type merged in here.
        try:
            for event_type, payload in stream_chat(
                session, messages, body.pinned_ids, body.excluded_ids
            ):
                yield f"data: {json.dumps({'type': event_type, **payload})}\n\n"
        except Exception:
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'code': 'chat_failed', 'message': 'Answer generation failed'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
