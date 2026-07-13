from app.services.chunking import chunk_pages


def test_empty_and_blank_pages_produce_nothing():
    assert chunk_pages([]) == []
    assert chunk_pages([(1, ""), (2, "   \n\n  ")]) == []


def test_short_page_is_single_chunk():
    chunks = chunk_pages([(1, "Breve testo di prova.")])
    assert chunks == [{"content": "Breve testo di prova.", "page_number": 1}]


def test_long_text_splits_with_overlap():
    paragraphs = [f"Paragrafo {i}. " + ("parola " * 40).strip() for i in range(10)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_pages([(1, text)], size=500, overlap=100)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["content"]) <= 500 + 100 + 2
        assert c["page_number"] == 1
    # overlap: each later chunk starts with the tail of the previous one
    tail = chunks[0]["content"][-100:]
    assert chunks[1]["content"].startswith(tail[: len(tail) // 2]) or tail in chunks[1]["content"]


def test_oversized_single_paragraph_is_hard_split():
    text = "x" * 2500
    chunks = chunk_pages([(None, text)], size=1000, overlap=200)
    assert len(chunks) >= 3
    assert all(len(c["content"]) <= 1200 for c in chunks)
    assert all(c["page_number"] is None for c in chunks)


def test_chunks_never_span_pages():
    chunks = chunk_pages([(1, "Pagina uno."), (2, "Pagina due.")])
    assert [c["page_number"] for c in chunks] == [1, 2]
