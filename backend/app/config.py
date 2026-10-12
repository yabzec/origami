from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_EMBEDDING_MODEL = "ollama/embeddinggemma"
LOCAL_EMBEDDING_API_BASE = "http://localhost:11434"
EMBEDDINGGEMMA_DIM = 768
DEFAULT_EMBEDDING_DIM = 1536


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://origami:origami@localhost:5432/origami"
    storage_path: Path = Path("./storage")
    derived_path: str = ""  # empty → <storage_path>/../derived (office previews, OCR companions)
    tmp_path: str = ""  # empty → <storage_path>/../tmp (scan sessions)
    jwt_secret: str = "dev-secret"
    jwt_expire_days: int = 30
    llm_model: str = "gemini/gemini-2.5-flash"
    vision_model: str = "gemini/gemini-2.5-flash"
    embedding_model: str = ""  # empty → LOCAL_EMBEDDING_MODEL on the Ollama container
    embedding_dim: int | None = None  # empty → 768 for embeddinggemma, else 1536
    gemini_api_key: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    vision_api_key: str = ""  # empty → llm_api_key (lets vision run on another provider than text)
    vision_api_base: str = ""  # empty → llm_api_base (unless vision_api_key is set)
    embedding_api_key: str = ""
    embedding_api_base: str = ""
    default_ocr_languages: str = "ita+eng"
    primary_language: str = "it"
    default_translation_language: str = ""  # ISO 639-1; empty → primary_language
    llm_tpm_limit: int = 0  # provider tokens-per-minute limit for translation; 0 = no throttle
    translation_segment_chars: int = 6000  # max source characters per translation call
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
    public_url: str = ""  # e.g. https://origami.example.com — public server URL handed to the client scanner agent; empty → request base URL
    agent_dist_dir: Path = Path("../agent/dist")  # built agent binaries served by /api/agent/download

    @field_validator("embedding_dim", mode="before")
    @classmethod
    def _empty_dim_means_default(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_primary_language() -> str:
    """Target language (ISO 639-1) for summaries and translations.

    Single lookup point: a future per-user profile setting replaces this body.
    """
    return get_settings().primary_language


def get_default_translation_language() -> str:
    """Configured default translation target (not checked against installed languages)."""
    settings = get_settings()
    return settings.default_translation_language.strip() or settings.primary_language


@dataclass(frozen=True)
class EmbeddingConfig:
    model: str
    dim: int
    api_key: str
    api_base: str
    scheme: str  # query/document prefix style: "none" or "embeddinggemma-v1"

    @property
    def is_ollama(self) -> bool:
        return self.model.startswith("ollama/")

    @property
    def ollama_name(self) -> str:
        name = self.model.split("/", 1)[1]
        return name if ":" in name else f"{name}:latest"


def embedding_scheme(model: str) -> str:
    return "embeddinggemma-v1" if "embeddinggemma" in model.lower() else "none"


def resolve_embedding(settings: Settings | None = None) -> EmbeddingConfig:
    """Embedding model, dimension, credentials and prefix scheme after defaults and fallbacks."""
    settings = settings or get_settings()
    model = settings.embedding_model.strip() or LOCAL_EMBEDDING_MODEL
    scheme = embedding_scheme(model)
    if model.startswith("ollama/"):
        # LLM_API_BASE may belong to another provider: Ollama gets its own base only.
        api_key, api_base = "", settings.embedding_api_base or LOCAL_EMBEDDING_API_BASE
    else:
        api_key = settings.embedding_api_key or settings.llm_api_key
        api_base = settings.embedding_api_base or settings.llm_api_base
    default_dim = EMBEDDINGGEMMA_DIM if scheme == "embeddinggemma-v1" else DEFAULT_EMBEDDING_DIM
    return EmbeddingConfig(model, settings.embedding_dim or default_dim, api_key, api_base, scheme)
