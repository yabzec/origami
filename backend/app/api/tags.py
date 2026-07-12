from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.models import Tag

router = APIRouter(
    prefix="/api/tags", tags=["tags"], dependencies=[Depends(get_current_user)]
)


class TagCreate(BaseModel):
    name: str
    color: str = "#888888"


class TagPatch(BaseModel):
    name: str | None = None
    color: str | None = None


@router.post("", status_code=201)
def create_tag(body: TagCreate, session: Session = Depends(get_session)) -> Tag:
    tag = Tag(name=body.name, color=body.color)
    session.add(tag)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_tag", "Tag with same name exists")
    session.refresh(tag)
    return tag


@router.get("")
def list_tags(session: Session = Depends(get_session)) -> list[Tag]:
    return list(session.exec(select(Tag)))


@router.patch("/{tag_id}")
def update_tag(tag_id: int, body: TagPatch, session: Session = Depends(get_session)) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise api_error(404, "not_found", f"Tag {tag_id} not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(tag, key, value)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_tag", "Tag with same name exists")
    session.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=204)
def delete_tag(tag_id: int, session: Session = Depends(get_session)) -> None:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise api_error(404, "not_found", f"Tag {tag_id} not found")
    session.delete(tag)
    session.commit()
