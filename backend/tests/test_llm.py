from types import SimpleNamespace

import litellm

from app.services import llm, local_models


def test_embed_returns_vectors_in_input_order(monkeypatch):
    from app.config import get_settings

    # Set the model explicitly rather than relying on the default: a developer's
    # local .env overrides it, and this test asserts on the value passed through.
    get_settings.cache_clear()
    monkeypatch.setenv("EMBEDDING_MODEL", "openai/@cf/baai/bge-m3")

    captured = {}

    def fake_embedding(model, input, **kwargs):
        captured["model"] = model
        captured["input"] = list(input)
        data = [{"index": i, "embedding": [float(i)] * 3} for i in range(len(input))]
        return SimpleNamespace(data=list(reversed(data)))  # out of order on purpose

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    vectors = llm.embed(["a", "b"])

    assert vectors == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]  # re-sorted by index
    assert captured["input"] == ["a", "b"]
    assert captured["model"] == "openai/@cf/baai/bge-m3"
    get_settings.cache_clear()


def test_embed_passes_embedding_credentials(monkeypatch):
    """The embedding endpoint is Cloudflare's, not the chat provider's.

    Chat and embeddings are different providers here, so `embed` must send
    EMBEDDING_API_* and never fall back to the chat LLM's key/base.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("EMBEDDING_API_KEY", "cf-token")
    monkeypatch.setenv(
        "EMBEDDING_API_BASE", "https://api.cloudflare.com/client/v4/accounts/abc/ai/v1"
    )
    monkeypatch.setenv("LLM_API_KEY", "gemini-key-must-not-leak-here")

    captured = {}

    def fake_embedding(model, input, api_key=None, api_base=None):
        captured["api_key"] = api_key
        captured["api_base"] = api_base
        return SimpleNamespace(data=[{"index": 0, "embedding": [0.1, 0.2]}])

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    llm.embed(["hello"])

    assert captured["api_key"] == "cf-token"
    assert captured["api_base"] == "https://api.cloudflare.com/client/v4/accounts/abc/ai/v1"
    get_settings.cache_clear()


def test_embed_short_circuits_on_empty_input(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("no network call should be made for an empty batch")

    monkeypatch.setattr(litellm, "embedding", explode)
    assert llm.embed([]) == []


def test_describe_text(monkeypatch):
    captured = {}

    def fake_completion(model, messages):
        captured["model"] = model
        captured["content"] = messages[0]["content"]
        msg = SimpleNamespace(content="  Una fattura del 2026.  ")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    result = llm.describe(text="FATTURA n. 42 del 2026...")
    assert result == "Una fattura del 2026."
    assert "FATTURA n. 42" in captured["content"]
    assert captured["model"] == "gemini/gemini-2.5-flash"


def test_describe_image_delegates_to_local_model(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    captured = {}

    def fake_describe_image(image_path, prompt):
        captured["image_path"] = image_path
        captured["prompt"] = prompt
        return "  Una foto di una ricevuta.  "

    monkeypatch.setattr(local_models, "describe_image", fake_describe_image)
    result = llm.describe(image_path=img)

    assert result == "Una foto di una ricevuta."  # stripped
    assert captured["image_path"] == img
    assert captured["prompt"] == llm.DESCRIBE_PROMPT  # same prompt as the text branch


def test_describe_image_makes_no_litellm_call(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")

    def explode(*args, **kwargs):
        raise AssertionError("vision must not reach litellm — it is local now")

    monkeypatch.setattr(litellm, "completion", explode)
    monkeypatch.setattr(local_models, "describe_image", lambda image_path, prompt: "ok")
    assert llm.describe(image_path=img) == "ok"


def test_complete_non_stream(monkeypatch):
    def fake_completion(model, messages, stream=False):
        assert stream is False
        assert model == "gemini/gemini-2.5-flash"
        msg = SimpleNamespace(content="Risposta completa.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    assert llm.complete([{"role": "user", "content": "ciao"}]) == "Risposta completa."


def test_complete_stream_yields_deltas(monkeypatch):
    def fake_completion(model, messages, stream=False):
        assert stream is True

        def chunks():
            for piece in ["Ecco ", None, "la risposta.", ""]:
                delta = SimpleNamespace(content=piece)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

        return chunks()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    deltas = list(llm.complete([{"role": "user", "content": "ciao"}], stream=True))
    assert deltas == ["Ecco ", "la risposta."]  # None/empty deltas filtered out


def test_complete_passes_api_key_and_base(monkeypatch):
    import litellm

    from app.config import get_settings
    from app.services import llm

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:11434")

    captured = {}

    def fake_completion(model, messages, stream=False, api_key=None, api_base=None):
        captured["api_key"] = api_key
        captured["api_base"] = api_base
        msg = type("M", (), {"content": "ok"})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    llm.complete([{"role": "user", "content": "hi"}])
    assert captured["api_key"] == "sk-test"
    assert captured["api_base"] == "http://localhost:11434"
    get_settings.cache_clear()
