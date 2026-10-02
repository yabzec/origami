from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


RETRY_DELAYS: list[int] = [30, 120, 600, 1800]  # seconds to wait after failures 1..4
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1  # 5 attempts in total


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"  # superseded (re-process); never claimed again


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    status: str = Field(default=JobStatus.queued, index=True)
    attempts: int = 0
    max_attempts: int = MAX_ATTEMPTS
    run_at: datetime = Field(default_factory=utcnow)
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
