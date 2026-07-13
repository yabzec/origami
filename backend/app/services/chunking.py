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
