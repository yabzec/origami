import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.services.auth import decode_token

bearer = HTTPBearer(auto_error=False)


def api_error(status: int, code: str, message: str, detail=None) -> HTTPException:
    """Build an HTTPException whose body the registered exception handler
    (see app.api.error_handlers) flattens to a top-level
    ``{"error": {"code", "message", "detail"}}`` response instead of
    FastAPI's default ``{"detail": ...}`` wrapping.
    """
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "detail": detail}},
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_session),
) -> User:
    if creds is None:
        raise api_error(401, "unauthorized", "Missing bearer token")
    try:
        payload = decode_token(creds.credentials)
    except pyjwt.InvalidTokenError:
        raise api_error(401, "unauthorized", "Invalid or expired token")
    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise api_error(401, "unauthorized", "Unknown user")
    return user


def get_current_user_flexible(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    token: str | None = None,
    session: Session = Depends(get_session),
) -> User:
    """Auth via Bearer header OR ?token= query param (browser-native resource loads)."""
    raw = creds.credentials if creds is not None else token
    if raw is None:
        raise api_error(401, "unauthorized", "Missing bearer token")
    try:
        payload = decode_token(raw)
    except pyjwt.InvalidTokenError:
        raise api_error(401, "unauthorized", "Invalid or expired token")
    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise api_error(401, "unauthorized", "Unknown user")
    return user
