import math

import pytest

from app.services import local_models

pytestmark = pytest.mark.slow


def test_embed_texts_returns_normalized_1024_dim_vectors_in_order():
    vectors = local_models.embed_texts(["prima frase", "second sentence"])

    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)
    for v in vectors:
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-3)  # normalize_embeddings=True
    assert vectors[0] != vectors[1]  # order preserved, not the same vector twice


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


def test_embed_texts_accepts_empty_list():
    assert local_models.embed_texts([]) == []
