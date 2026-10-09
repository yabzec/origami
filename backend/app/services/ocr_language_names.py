"""English names for common Tesseract language codes; unmapped codes are shown as the code."""

OCR_LANGUAGE_NAMES = {
    "afr": "Afrikaans", "ara": "Arabic", "bul": "Bulgarian", "cat": "Catalan", "ces": "Czech",
    "chi_sim": "Chinese (Simplified)", "chi_tra": "Chinese (Traditional)", "dan": "Danish",
    "deu": "German", "ell": "Greek", "eng": "English", "est": "Estonian", "fin": "Finnish",
    "fra": "French", "heb": "Hebrew", "hin": "Hindi", "hrv": "Croatian", "hun": "Hungarian",
    "ind": "Indonesian", "ita": "Italian", "jpn": "Japanese", "kor": "Korean", "lat": "Latin",
    "lav": "Latvian", "lit": "Lithuanian", "nld": "Dutch", "nor": "Norwegian", "pol": "Polish",
    "por": "Portuguese", "ron": "Romanian", "rus": "Russian", "slk": "Slovak", "slv": "Slovenian",
    "spa": "Spanish", "srp": "Serbian", "swe": "Swedish", "tur": "Turkish", "ukr": "Ukrainian",
    "vie": "Vietnamese",
}


def ocr_language_name(code: str) -> str:
    return OCR_LANGUAGE_NAMES.get(code, code)


TESSERACT_TO_ISO = {
    "afr": "af", "ara": "ar", "bul": "bg", "cat": "ca", "ces": "cs", "chi_sim": "zh", "chi_tra": "zh",
    "dan": "da", "deu": "de", "ell": "el", "eng": "en", "est": "et", "fin": "fi", "fra": "fr",
    "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "ind": "id", "ita": "it", "jpn": "ja",
    "kor": "ko", "lat": "la", "lav": "lv", "lit": "lt", "nld": "nl", "nor": "no", "pol": "pl",
    "por": "pt", "ron": "ro", "rus": "ru", "slk": "sk", "slv": "sl", "spa": "es", "srp": "sr",
    "swe": "sv", "tur": "tr", "ukr": "uk", "vie": "vi",
}

ISO_LANGUAGE_NAMES = {
    iso: ("Chinese" if iso == "zh" else OCR_LANGUAGE_NAMES[code]) for code, iso in TESSERACT_TO_ISO.items()
}


def iso_language(tesseract_code: str) -> str | None:
    """ISO 639-1 code of a Tesseract language; None for scripts and special packs."""
    return TESSERACT_TO_ISO.get(tesseract_code)


def iso_language_name(iso: str) -> str:
    return ISO_LANGUAGE_NAMES.get(iso, iso)
