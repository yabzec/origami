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
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_model_revision: str = "main"
    vision_model_name: str = "vikhyatk/moondream2"
    vision_model_revision: str = "6b714b26eea5cbd9f31e4edb2541c170afa935ba"
    gemini_api_key: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    default_ocr_languages: str = "ita+eng"
    rag_top_k: int = 8
    rag_relevance_floor: float = 0.35
    cors_origins: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
