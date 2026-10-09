from fastapi import APIRouter, Depends

from app.api.deps import api_error, get_current_user
from app.config import get_settings
from app.services.ocr import available_languages, unknown_languages
from app.services.ocr_language_names import ocr_language_name

router = APIRouter(prefix="/api/ocr", tags=["ocr"], dependencies=[Depends(get_current_user)])


def check_ocr_languages(value: str | None) -> None:
    """422 when a client-supplied Tesseract language string names an uninstalled language."""
    if value is None:
        return
    unknown = unknown_languages(value)
    if unknown:
        raise api_error(422, "unknown_ocr_language", f"Unknown OCR language: {unknown[0]}")


def default_ocr_languages() -> str:
    """DEFAULT_OCR_LANGUAGES limited to installed languages; else the first installed one."""
    codes = available_languages()
    configured = [c for c in get_settings().default_ocr_languages.split("+") if c in codes]
    return "+".join(configured) or (codes[0] if codes else "")


@router.get("/languages")
def list_ocr_languages() -> dict:
    return {
        "languages": [{"code": c, "name": ocr_language_name(c)} for c in available_languages()],
        "default": default_ocr_languages(),
    }
