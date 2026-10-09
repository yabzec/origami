from app.services.chunking import chunk_pages, page_texts_from_chunks, segment_pages


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


def _roundtrip(pages, size=100, overlap=20):
    chunks = [(c["page_number"], c["content"]) for c in chunk_pages(pages, size=size, overlap=overlap)]
    return page_texts_from_chunks(chunks, overlap=overlap)


def test_page_texts_roundtrip_paragraphs():
    paragraphs = [f"Paragraph number {i} has some words in it." for i in range(12)]
    page = "\n\n".join(paragraphs)
    assert _roundtrip([(1, page), (2, "Short page.")]) == [(1, page), (2, "Short page.")]


def test_page_texts_roundtrip_hard_split_paragraph():
    long_paragraph = "".join(f"w{i:03d} " for i in range(90)).strip()  # ~450 chars, no blank lines
    page = f"Intro line.\n\n{long_paragraph}\n\nOutro line."
    assert _roundtrip([(3, page)]) == [(3, page)]


def test_page_texts_roundtrip_overlap_starting_with_space():
    # the overlap tail of a chunk can begin with whitespace, which chunk_pages strips
    page = "\n\n".join("a" * 75 + " " + "b" * 19 for _ in range(6))
    assert _roundtrip([(1, page)]) == [(1, page)]


def test_page_texts_keeps_chunk_when_overlap_does_not_match():
    assert page_texts_from_chunks([(1, "first"), (1, "unrelated")]) == [(1, "first\n\nunrelated")]


def test_page_texts_none_page_numbers_group_together():
    assert page_texts_from_chunks([(None, "a"), (None, "b")], overlap=0) == [(None, "a\n\nb")]


def test_segment_pages_splits_long_pages_without_overlap():
    page = "\n\n".join(["x" * 40] * 5)  # 5 paragraphs of 40 chars
    segments = segment_pages([(1, page), (2, "   "), (3, "short")], max_chars=100)
    assert segments == [
        (1, "x" * 40 + "\n\n" + "x" * 40),
        (1, "x" * 40 + "\n\n" + "x" * 40),
        (1, "x" * 40),
        (3, "short"),
    ]
