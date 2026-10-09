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
