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
