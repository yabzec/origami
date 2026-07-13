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
    default_ocr_languages: str = "ita+eng"
    rag_top_k: int = 8
    rag_relevance_floor: float = 0.35


@lru_cache
def get_settings() -> Settings:
    return Settings()
