from types import SimpleNamespace

import litellm

from app.services import llm, local_models


def test_embed_delegates_to_local_model(monkeypatch):
    captured = {}

    def fake_embed_texts(texts):
        captured["texts"] = list(texts)
        return [[0.5, 0.5], [0.1, 0.9]]

    monkeypatch.setattr(local_models, "embed_texts", fake_embed_texts)
    vectors = llm.embed(["a", "b"])

    assert vectors == [[0.5, 0.5], [0.1, 0.9]]  # returned unchanged, order preserved
    assert captured["texts"] == ["a", "b"]


def test_embed_makes_no_litellm_call(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("embedding must not reach litellm — it is local now")

    monkeypatch.setattr(litellm, "embedding", explode)
    monkeypatch.setattr(local_models, "embed_texts", lambda texts: [[0.0]] * len(texts))
    assert llm.embed(["a"]) == [[0.0]]


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
