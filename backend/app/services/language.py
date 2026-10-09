"""Local (no-LLM) language detection for extracted document text."""

from functools import lru_cache

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

DETECT_INPUT_CHARS = 5000
MIN_LETTERS = 20  # below this, detection is noise

# Languages a personal archive in Europe realistically holds; a closed set keeps memory small
# and accuracy high. Extend when needed.
SUPPORTED = (
    Language.ITALIAN, Language.ENGLISH, Language.GERMAN, Language.FRENCH, Language.SPANISH,
    Language.PORTUGUESE, Language.DUTCH, Language.POLISH, Language.ROMANIAN, Language.CROATIAN,
    Language.SLOVENE, Language.CZECH, Language.SWEDISH, Language.DANISH, Language.BOKMAL,
    Language.FINNISH, Language.HUNGARIAN, Language.GREEK, Language.RUSSIAN, Language.UKRAINIAN,
    Language.TURKISH, Language.ARABIC, Language.CHINESE, Language.JAPANESE,
)


@lru_cache
def _detector() -> LanguageDetector:
    return LanguageDetectorBuilder.from_languages(*SUPPORTED).with_minimum_relative_distance(0.1).build()


def detect_language(text: str | None) -> str | None:
    """ISO 639-1 code of the text's language, or None when empty or not confident."""
    sample = (text or "")[:DETECT_INPUT_CHARS]
    if sum(ch.isalpha() for ch in sample) < MIN_LETTERS:
        return None
    language = _detector().detect_language_of(sample)
    return language.iso_code_639_1.name.lower() if language is not None else None
