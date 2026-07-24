from types import SimpleNamespace

import litellm

from app.services import llm


def test_embed_returns_vectors_in_input_order(monkeypatch):
    def fake_embedding(model, input, dimensions):
        assert model == "gemini/gemini-embedding-001"
        assert dimensions == 1536
        data = [{"index": i, "embedding": [float(i)] * 3} for i in range(len(input))]
        return SimpleNamespace(data=list(reversed(data)))  # out of order on purpose

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    vectors = llm.embed(["a", "b"])
    assert vectors == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]


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


def test_describe_image(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    captured = {}

    def fake_completion(model, messages):
        captured["model"] = model
        captured["parts"] = messages[0]["content"]
        msg = SimpleNamespace(content="A receipt photo.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    result = llm.describe(image_path=img)
    assert result == "A receipt photo."
    kinds = [p["type"] for p in captured["parts"]]
    assert kinds == ["text", "image_url"]
    assert captured["parts"][1]["image_url"]["url"].startswith("data:image/png;base64,")


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


def test_embed_falls_back_to_llm_key(monkeypatch):
    import litellm

    from app.config import get_settings
    from app.services import llm

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "sk-shared")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    captured = {}

    def fake_embedding(model, input, dimensions, api_key=None, api_base=None):
        captured["api_key"] = api_key
        return type("R", (), {"data": [{"index": 0, "embedding": [0.1] * dimensions}]})()

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    llm.embed(["hello"])
    assert captured["api_key"] == "sk-shared"
    get_settings.cache_clear()
