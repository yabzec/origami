from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://origami:origami@localhost:5432/origami"
    storage_path: Path = Path("./storage")
    jwt_secret: str = "dev-secret"
    jwt_expire_days: int = 30
    llm_model: str = "gemini/gemini-2.5-flash"
    vision_model: str = "gemini/gemini-2.5-flash"
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dim: int = 1536
    gemini_api_key: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    embedding_api_key: str = ""
    embedding_api_base: str = ""
    default_ocr_languages: str = "ita+eng"
    primary_language: str = "it"
    rag_top_k: int = 8
    rag_relevance_floor: float = 0.35
    cors_origins: str = "*"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_app_password: str = ""  # Gmail: an app password (needs 2-step verification)
    smtp_from: str = ""  # empty → smtp_user
    app_base_url: str = ""  # e.g. http://origami.lan:8000 — adds document links to emails
    soffice_path: str = "soffice"  # LibreOffice binary used to convert office documents to PDF


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_primary_language() -> str:
    """Target language (ISO 639-1) for summaries and translations.

    Single lookup point: a future per-user profile setting replaces this body.
    """
    return get_settings().primary_language
