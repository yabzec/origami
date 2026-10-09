from types import SimpleNamespace

import httpx
import litellm
import pytest

from app.services import llm


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_budget_under_limit_does_not_wait():
    t = FakeTime()
    budget = llm.TokenBudget(1000, t.clock, t.sleep)
    budget.acquire(400)
    budget.acquire(500)
    assert t.sleeps == []


def test_budget_over_limit_waits_for_window():
    t = FakeTime()
    budget = llm.TokenBudget(1000, t.clock, t.sleep)
    budget.acquire(800)
    t.now = 10.0
    budget.acquire(500)
    assert t.now >= 60.0  # waited until the first entry aged out


def test_budget_request_bigger_than_limit_does_not_hang():
    t = FakeTime()
    budget = llm.TokenBudget(1000, t.clock, t.sleep)
    budget.acquire(5000)  # capped to the limit, empty window: proceeds
    assert t.sleeps == []


def test_unlimited_budget_returns_none():
    t = FakeTime()
    assert llm.TokenBudget(0, t.clock, t.sleep).acquire(10**9) is None


def _rate_limit(headers=None, message="Rate limit reached"):
    response = httpx.Response(429, headers=headers or {}, request=httpx.Request("POST", "http://x"))
    return litellm.RateLimitError(message=message, llm_provider="groq", model="m", response=response)


def test_rate_limit_wait_from_header_message_or_default():
    assert llm.rate_limit_wait(_rate_limit({"retry-after": "7"})) == 7.0
    assert llm.rate_limit_wait(_rate_limit(message="Please try again in 7.5s.")) == 7.5
    assert llm.rate_limit_wait(_rate_limit(message="Please try again in 1m2.5s.")) == 62.5
    assert llm.rate_limit_wait(_rate_limit()) == 60.0


def _ok(content="Ciao"):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(total_tokens=42))


@pytest.fixture
def fake_time(monkeypatch):
    t = FakeTime()
    monkeypatch.setattr(llm, "_clock", t.clock)
    monkeypatch.setattr(llm, "_sleep", t.sleep)
    llm.reset_translate_budget()
    yield t
    llm.reset_translate_budget()


def test_translate_retries_on_rate_limit(monkeypatch, fake_time):
    replies = iter([_rate_limit({"retry-after": "3"}), _ok("Fattura")])

    def completion(**kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(litellm, "completion", completion)
    assert llm.translate("Rechnung", "it") == "Fattura"
    assert fake_time.sleeps == [3.0]


def test_translate_gives_up_after_retries(monkeypatch, fake_time):
    calls = []

    def completion(**kwargs):
        calls.append(1)
        raise _rate_limit({"retry-after": "1"})

    monkeypatch.setattr(litellm, "completion", completion)
    with pytest.raises(litellm.RateLimitError):
        llm.translate("Rechnung", "it")
    assert len(calls) == llm.RATE_LIMIT_RETRIES + 1


def test_translate_throttles_to_tpm_limit(monkeypatch, fake_time):
    from app.config import get_settings

    monkeypatch.setenv("LLM_TPM_LIMIT", "100")
    get_settings.cache_clear()
    monkeypatch.setattr(litellm, "token_counter", lambda model, text: 40)  # estimate 80 per call
    monkeypatch.setattr(litellm, "completion", lambda **kw: _ok())
    try:
        llm.translate("a", "it")
        llm.translate("b", "it")  # 42 actual + 80 estimate > 100: waits
    finally:
        get_settings.cache_clear()
    assert fake_time.sleeps and fake_time.now >= 60.0
