def chunk_pages(
    pages: list[tuple[int | None, str]], size: int = 1000, overlap: int = 200
) -> list[dict]:
    """Split page texts into ~size-char chunks on paragraph boundaries.

    Chunks never span pages, so every chunk carries an exact page_number.
    Consecutive chunks from the same page share `overlap` trailing/leading chars.
    """
    chunks: list[dict] = []
    for page_number, text in pages:
        for piece in _split_text(text, size, overlap):
            chunks.append({"content": piece, "page_number": page_number})
    return chunks


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces: list[str] = []
    current = ""

    def flush() -> str:
        """Push current piece; return the overlap tail to seed the next one."""
        nonlocal current
        if not current:
            return ""
        pieces.append(current)
        tail = current[-overlap:] if overlap else ""
        current = ""
        return tail

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= size:
            current = candidate
            continue
        tail = flush()
        # hard-split paragraphs that alone exceed size
        while len(para) > size:
            piece = f"{tail}\n\n{para[:size]}" if tail else para[:size]
            pieces.append(piece)
            step = size - overlap if overlap and size > overlap else size
            para = para[step:]
            tail = ""
        current = f"{tail}\n\n{para}".strip() if tail else para
    if current:
        pieces.append(current)
    return pieces


def page_texts_from_chunks(
    chunks: list[tuple[int | None, str]], overlap: int = 200
) -> list[tuple[int | None, str]]:
    """Rebuild page texts from chunk_pages() output by removing the overlap between chunks.

    When a chunk does not start with the previous chunk's tail, it is kept whole after a
    paragraph break: duplicating a little text is better than losing any.
    """
    pages: list[list] = []  # [page_number, text, previous chunk]
    for page_number, content in chunks:
        if not pages or pages[-1][0] != page_number:
            pages.append([page_number, content, content])
            continue
        entry = pages[-1]
        tail = entry[2][-overlap:] if overlap else ""
        for candidate in (tail, tail.lstrip()):
            if candidate and content.startswith(candidate):
                entry[1] += content[len(candidate):]
                break
        else:
            entry[1] += "\n\n" + content
        entry[2] = content
    return [(page_number, text) for page_number, text, _ in pages]


def segment_pages(
    pages: list[tuple[int | None, str]], max_chars: int
) -> list[tuple[int | None, str]]:
    """Translation units: one per page, long pages split on paragraphs, no overlap."""
    return [
        (page_number, piece)
        for page_number, text in pages
        for piece in _split_text(text, max_chars, 0)
    ]
