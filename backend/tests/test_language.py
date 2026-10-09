from app.services.language import detect_language


def test_detects_italian_english_german():
    assert detect_language("Gentile cliente, in allegato trova la fattura del mese di marzo.") == "it"
    assert detect_language("Dear customer, please find attached the invoice for March.") == "en"
    assert detect_language("Sehr geehrte Damen und Herren, anbei die Rechnung für März.") == "de"


def test_empty_or_tiny_text_is_none():
    assert detect_language("") is None
    assert detect_language(None) is None
    assert detect_language("   \n ") is None
    assert detect_language("12 34 / 56") is None


def test_only_looks_at_the_first_5000_chars():
    text = "Gentile cliente, ecco la fattura del mese. " * 200 + "Dear customer " * 2000
    assert detect_language(text) == "it"
