import math

import pytest

from app.config import get_settings
from app.services import local_models


def test_describe_image_pins_revision_and_trusts_remote_code(tmp_path, monkeypatch):
    """Fast guard for the vision-model pinning security argument.

    moondream2 executes arbitrary code from its HuggingFace repo, so
    `trust_remote_code=True` must always be paired with a pinned `revision=`.
    This does not load real weights: `from_pretrained` is monkeypatched to
    capture its kwargs and return a stub with a `.query()` method.
    """
    from PIL import Image

    img_path = tmp_path / "blank.png"
    Image.new("RGB", (16, 16), "white").save(img_path)

    captured = {}

    class FakeModel:
        def query(self, image, prompt):
            return {"answer": "test"}

    def fake_from_pretrained(name, revision=None, trust_remote_code=None, torch_dtype=None):
        captured["name"] = name
        captured["revision"] = revision
        captured["trust_remote_code"] = trust_remote_code
        return FakeModel()

    monkeypatch.setattr(
        "transformers.AutoModelForCausalLM.from_pretrained", fake_from_pretrained
    )

    answer = local_models.describe_image(img_path, "What is this?")

    assert answer == "test"
    assert captured["revision"] == get_settings().vision_model_revision
    assert captured["trust_remote_code"] is True


@pytest.mark.slow
def test_embed_texts_returns_normalized_1024_dim_vectors_in_order():
    vectors = local_models.embed_texts(["prima frase", "second sentence"])

    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)
    for v in vectors:
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-3)  # normalize_embeddings=True
    assert vectors[0] != vectors[1]  # order preserved, not the same vector twice


@pytest.mark.slow
def test_embed_texts_places_related_multilingual_text_closer_than_unrelated():
    # Weak semantic assertion: catches a silently wrong model or missing normalization,
    # without asserting on exact float values.
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))

    invoice_it, invoice_en, unrelated = local_models.embed_texts(
        [
            "Fattura per la fornitura di energia elettrica, importo 120 euro.",
            "Invoice for the supply of electricity, amount 120 euros.",
            "Le ricette della nonna per la torta di mele.",
        ]
    )

    assert cos(invoice_it, invoice_en) > cos(invoice_it, unrelated)


@pytest.mark.slow
def test_embed_texts_accepts_empty_list():
    assert local_models.embed_texts([]) == []


@pytest.mark.slow
def test_describe_image_returns_non_empty_text(tmp_path):
    from PIL import Image, ImageDraw

    img_path = tmp_path / "receipt.png"
    image = Image.new("RGB", (640, 320), "white")
    ImageDraw.Draw(image).text((20, 140), "TOTALE 42,00 EUR", fill="black")
    image.save(img_path)

    answer = local_models.describe_image(img_path, "What does this image show?")

    assert isinstance(answer, str)
    assert answer.strip()


@pytest.mark.slow
def test_describe_image_releases_the_model_after_the_call(tmp_path):
    from PIL import Image

    img_path = tmp_path / "blank.png"
    Image.new("RGB", (64, 64), "white").save(img_path)

    local_models.describe_image(img_path, "Describe this.")

    # The vision model must not be cached anywhere: ~3.7GB of resident RAM per
    # process is the difference between fitting in 16GB and not.
    assert not any(
        name for name in vars(local_models) if name.startswith("_vision")
        and getattr(local_models, name) is not None
    )
