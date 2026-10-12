import os


def test_tests_pin_remote_embedding_model():
    """A developer's exported EMBEDDING_MODEL must not send tests to a real Ollama."""
    assert os.environ["EMBEDDING_MODEL"] == "gemini/gemini-embedding-001"
    assert "EMBEDDING_DIM" not in os.environ
    assert "EMBEDDING_API_BASE" not in os.environ
