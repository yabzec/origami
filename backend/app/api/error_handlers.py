"""Exception handlers that flatten all error responses to a single top-level
``{"error": {"code", "message", "detail"}}`` envelope.

FastAPI's default handling wraps ``HTTPException.detail`` under its own
``detail`` key on the wire (``{"detail": ...}``). ``app.api.deps.api_error()``
already builds the desired ``{"error": {...}}`` payload and stores it as
``HTTPException.detail`` — the handlers below unwrap it so it lands at the
top level of the JSON body instead of being nested under ``detail``.
"""

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _is_flat_error_payload(detail: object) -> bool:
    return (
        isinstance(detail, dict)
        and set(detail.keys()) == {"error"}
        and isinstance(detail["error"], dict)
        and {"code", "message", "detail"} <= detail["error"].keys()
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc: HTTPException) -> JSONResponse:
        if _is_flat_error_payload(exc.detail):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "http_error",
                    "message": str(exc.detail),
                    "detail": None,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "detail": jsonable_encoder(exc.errors()),
                }
            },
        )

    from app.services.scanner import ScannerError

    @app.exception_handler(ScannerError)
    async def scanner_error_handler(request, exc: ScannerError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "detail": None}},
        )
