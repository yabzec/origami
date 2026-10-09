import logging

from fastapi import APIRouter, Depends

from app.api.deps import api_error, get_current_user
from app.config import get_default_translation_language, get_settings
from app.services.ocr import available_languages, unknown_languages
from app.services.ocr_language_names import iso_language, iso_language_name, ocr_language_name

log = logging.getLogger("origami.ocr")

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


def translation_languages() -> list[str]:
    """ISO 639-1 targets: installed Tesseract languages that have one, sorted by name."""
    codes = {iso for code in available_languages() if (iso := iso_language(code))}
    return sorted(codes, key=iso_language_name)


def default_translation_language() -> str:
    """DEFAULT_TRANSLATION_LANGUAGE if installed; else the first target (warning)."""
    wanted = get_default_translation_language()
    codes = translation_languages()
    if wanted in codes or not codes:
        return wanted
    log.warning("Translation language %r is not installed; using %r", wanted, codes[0])
    return codes[0]


def check_translation_language(value: str | None) -> None:
    if value is None:
        return
    if value not in translation_languages():
        raise api_error(
            422, "unknown_translation_language", f"Unknown translation language: {value}"
        )


@router.get("/languages")
def list_ocr_languages() -> dict:
    return {
        "languages": [{"code": c, "name": ocr_language_name(c)} for c in available_languages()],
        "default": default_ocr_languages(),
        "translation_languages": [
            {"code": c, "name": iso_language_name(c)} for c in translation_languages()
        ],
        "translation_default": default_translation_language(),
    }
