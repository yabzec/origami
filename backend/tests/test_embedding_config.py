from app.config import EmbeddingConfig, Settings, embedding_scheme, resolve_embedding


def make(**values) -> Settings:
    base = {"embedding_model": "", "embedding_dim": None, "embedding_api_key": "", "embedding_api_base": "",
            "llm_api_key": "", "llm_api_base": ""}
    return Settings(_env_file=None, **{**base, **values})


def test_empty_model_resolves_to_local_default():
    cfg = resolve_embedding(make())
    assert cfg == EmbeddingConfig(
        model="ollama/embeddinggemma", dim=768, api_key="", api_base="http://localhost:11434",
        scheme="embeddinggemma-v1",
    )
    assert cfg.is_ollama
    assert cfg.ollama_name == "embeddinggemma:latest"


def test_local_default_ignores_llm_key_and_base():
    cfg = resolve_embedding(make(llm_api_key="sk-groq", llm_api_base="https://api.groq.com/openai/v1"))
    assert cfg.api_key == ""
    assert cfg.api_base == "http://localhost:11434"


def test_local_default_honors_embedding_api_base():
    cfg = resolve_embedding(make(embedding_api_base="http://nas:11434"))
    assert cfg.api_base == "http://nas:11434"


def test_explicit_remote_model_keeps_llm_fallback():
    cfg = resolve_embedding(make(embedding_model="gemini/gemini-embedding-001", llm_api_key="sk-shared"))
    assert cfg.model == "gemini/gemini-embedding-001"
    assert cfg.dim == 1536
    assert cfg.api_key == "sk-shared"
    assert cfg.scheme == "none"
    assert not cfg.is_ollama


def test_explicit_ollama_model_does_not_use_llm_base():
    cfg = resolve_embedding(make(embedding_model="ollama/qwen3-embedding:0.6b", embedding_dim=1024,
                                 llm_api_base="https://api.groq.com/openai/v1"))
    assert cfg.api_base == "http://localhost:11434"
    assert cfg.ollama_name == "qwen3-embedding:0.6b"
    assert cfg.dim == 1024
    assert cfg.scheme == "none"


def test_explicit_dim_wins():
    assert resolve_embedding(make(embedding_dim=512)).dim == 512


def test_scheme_from_model_name():
    assert embedding_scheme("ollama/embeddinggemma:300m") == "embeddinggemma-v1"
    assert embedding_scheme("ollama/EmbeddingGemma") == "embeddinggemma-v1"
    assert embedding_scheme("gemini/gemini-embedding-001") == "none"


def test_empty_dim_env_means_default(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "")
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    settings = Settings(_env_file=None)
    assert settings.embedding_dim is None
    assert resolve_embedding(settings).dim == 768
