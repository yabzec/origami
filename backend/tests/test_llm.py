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


def _completion_returning(content, captured):
    def fake_completion(model, messages):
        captured["model"] = model
        captured["content"] = messages[0]["content"]
        msg = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    return fake_completion


def test_describe_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        litellm,
        "completion",
        _completion_returning('{"summary": " Una fattura del 2026. ", "language": "DE"}', captured),
    )
    result = llm.describe(text="FATTURA n. 42 del 2026...")
    assert result == llm.Description("Una fattura del 2026.", "de")
    assert "FATTURA n. 42" in captured["content"]
    assert "Italian" in captured["content"]  # summary requested in the primary language
    assert captured["model"] == "gemini/gemini-2.5-flash"


def test_describe_image(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    captured = {}
    monkeypatch.setattr(
        litellm, "completion", _completion_returning('{"summary": "Uno scontrino.", "language": "it"}', captured)
    )
    result = llm.describe(image_path=img)
    assert result == llm.Description("Uno scontrino.", "it")
    kinds = [p["type"] for p in captured["content"]]
    assert kinds == ["text", "image_url"]
    assert captured["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_parse_description_handles_fenced_json():
    raw = '```json\n{"summary": "Contratto di affitto.", "language": "it"}\n```'
    assert llm.parse_description(raw) == llm.Description("Contratto di affitto.", "it")


def test_parse_description_falls_back_to_raw_text():
    assert llm.parse_description("  Just prose, no JSON.  ") == llm.Description("Just prose, no JSON.", None)
    assert llm.parse_description('{"summary": ""}') == llm.Description('{"summary": ""}', None)
    assert llm.parse_description('["not", "an", "object"]').language is None


def test_translate(monkeypatch):
    captured = {}
    monkeypatch.setattr(litellm, "completion", _completion_returning("  Fattura numero 5  ", captured))
    assert llm.translate("Rechnung Nummer 5", "it") == "Fattura numero 5"
    assert "Italian" in captured["content"]
    assert "Rechnung Nummer 5" in captured["content"]


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
    monkeypatch.setattr(llm, "get_settings", get_settings)  # env-driven test: undo pinned_settings
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
    monkeypatch.setattr(llm, "get_settings", get_settings)  # env-driven test: undo pinned_settings
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


def test_parse_description_rejects_null_summary():
    raw = '{"summary": null, "language": "it"}'
    assert llm.parse_description(raw) == llm.Description(raw, None)


def test_parse_description_unknown_language_is_none():
    assert llm.parse_description('{"summary": "Ok.", "language": "unknown"}') == llm.Description("Ok.", None)


def test_parse_description_normalizes_region_language():
    assert llm.parse_description('{"summary": "Ok.", "language": "de-DE"}') == llm.Description("Ok.", "de")
    assert llm.parse_description('{"summary": "Ok.", "language": "EN_us"}') == llm.Description("Ok.", "en")


def test_parse_description_extracts_json_from_prose():
    raw = 'Here is the result:\n{"summary": "Una fattura.", "language": "it"}\nHope it helps!'
    assert llm.parse_description(raw) == llm.Description("Una fattura.", "it")


def test_parse_description_none_content():
    assert llm.parse_description(None) == llm.Description("", None)


import io
import json as jsonlib
import urllib.request

import pytest

from app.config import Settings


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch):
    """Default settings without the developer's .env, so a real LLM_API_KEY (e.g. a Groq key)
    can't leak into the fakes. That would break the older tests whose fakes take no **kw."""
    settings = Settings(_env_file=None, llm_api_key="", llm_api_base="", gemini_api_key="")
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    return settings


def _use_settings(monkeypatch, **values):
    settings = Settings(
        _env_file=None,
        **{
            "llm_model": "groq/openai/gpt-oss-120b",
            "vision_model": "gemini/gemini-2.5-flash",
            "llm_api_key": "",
            "llm_api_base": "",
            "vision_api_key": "",
            "vision_api_base": "",
            "gemini_api_key": "",
            **values,
        },
    )
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    return settings


def _capturing_completion(captured):
    def fake_completion(model, messages, **kw):
        captured.append({"model": model, **kw})
        msg = SimpleNamespace(content='{"summary": "Uno scontrino.", "language": "it"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    return fake_completion


def _image(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    return img


def test_vision_call_uses_vision_credentials(monkeypatch, tmp_path):
    _use_settings(
        monkeypatch,
        llm_api_key="groq-key",
        llm_api_base="https://api.groq.com/openai/v1",
        vision_api_key="gemini-key",
    )
    captured = []
    monkeypatch.setattr(litellm, "completion", _capturing_completion(captured))
    llm.describe(image_path=_image(tmp_path))
    assert captured == [{"model": "gemini/gemini-2.5-flash", "api_key": "gemini-key"}]


def test_vision_call_falls_back_to_llm_credentials(monkeypatch, tmp_path):
    _use_settings(monkeypatch, llm_api_key="shared-key")
    captured = []
    monkeypatch.setattr(litellm, "completion", _capturing_completion(captured))
    llm.describe(image_path=_image(tmp_path))
    assert captured[0]["api_key"] == "shared-key"


def test_vision_call_falls_back_to_llm_key_and_base(monkeypatch, tmp_path):
    _use_settings(monkeypatch, llm_api_key="shared-key", llm_api_base="http://llm.local/v1")
    captured = []
    monkeypatch.setattr(litellm, "completion", _capturing_completion(captured))
    llm.describe(image_path=_image(tmp_path))
    assert captured[0]["api_key"] == "shared-key"
    assert captured[0]["api_base"] == "http://llm.local/v1"


def test_text_call_never_gets_vision_key(monkeypatch):
    _use_settings(monkeypatch, llm_api_key="groq-key", vision_api_key="gemini-key")
    captured = []
    monkeypatch.setattr(litellm, "completion", _capturing_completion(captured))
    llm.describe(text="Rechnung")
    assert captured == [{"model": "groq/openai/gpt-oss-120b", "api_key": "groq-key"}]


def _fake_urlopen(body, seen):
    def fake(request, timeout=None):
        seen.append({"url": request.full_url, "headers": dict(request.header_items()), "timeout": timeout})
        return io.BytesIO(jsonlib.dumps(body).encode())

    return fake


def test_list_models_groq(monkeypatch):
    _use_settings(monkeypatch, llm_api_key="groq-key")
    seen = []
    body = {
        "object": "list",
        "data": [
            {"id": "openai/gpt-oss-120b", "owned_by": "OpenAI", "context_window": 131072, "active": True},
            {"id": "whisper-large-v3"},
        ],
    }
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(body, seen))
    assert llm.model_provider() == "groq"
    assert llm.list_models() == [
        {"id": "openai/gpt-oss-120b", "owner": "OpenAI", "context_window": 131072, "active": True},
        {"id": "whisper-large-v3", "owner": None, "context_window": None, "active": None},
    ]
    assert seen[0]["url"] == "https://api.groq.com/openai/v1/models"
    assert seen[0]["headers"]["Authorization"] == "Bearer groq-key"
    assert seen[0]["timeout"] == 20


def test_list_models_groq_key_from_environment(monkeypatch):
    _use_settings(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "env-groq-key")
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"data": []}, seen))
    assert llm.list_models() == []
    assert seen[0]["headers"]["Authorization"] == "Bearer env-groq-key"


def test_list_models_custom_base(monkeypatch):
    _use_settings(monkeypatch, llm_model="openai/local", llm_api_base="http://localhost:8080/v1/", llm_api_key="k")
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"data": [{"id": "m"}]}, seen))
    assert [m["id"] for m in llm.list_models()] == ["m"]
    assert seen[0]["url"] == "http://localhost:8080/v1/models"


def test_list_models_gemini(monkeypatch):
    _use_settings(monkeypatch, llm_model="gemini/gemini-2.5-flash", gemini_api_key="g-key")
    seen = []
    body = {"models": [{"name": "models/gemini-2.5-flash", "inputTokenLimit": 1048576, "displayName": "Gemini 2.5 Flash"}]}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(body, seen))
    assert llm.list_models() == [
        {"id": "gemini-2.5-flash", "owner": None, "context_window": 1048576, "active": None}
    ]
    assert seen[0]["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models?")
    assert "key=g-key" in seen[0]["url"]
    assert "Authorization" not in seen[0]["headers"]


def test_list_models_unsupported_provider(monkeypatch):
    _use_settings(monkeypatch, llm_model="ollama/llama3.1")
    with pytest.raises(ValueError, match="Model listing not supported for provider 'ollama'"):
        llm.list_models()


def test_list_models_missing_key(monkeypatch):
    _use_settings(monkeypatch)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No API key for provider 'groq'"):
        llm.list_models()
