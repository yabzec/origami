import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def make_text_image(
    path: Path, text: str = "FATTURA 2026", size: tuple[int, int] = (1200, 400)
) -> Path:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.text((60, size[1] // 3), text, fill="black", font=ImageFont.truetype(FONT, 72))
    img.save(path)
    return path


import uuid


def basis_vector(index: int, dim: int = 1536) -> list[float]:
    vector = [0.0] * dim
    vector[index] = 1.0
    return vector


def seed_document(session, title: str, chunk_specs: list[dict], **doc_kwargs):
    """Insert a ready Document plus chunks. Each spec: {content, embedding?, page_number?, source?}."""
    from app.models import Chunk, ChunkSource, DocStatus, DocType, Document

    doc = Document(
        title=title,
        doc_type=doc_kwargs.pop("doc_type", DocType.text),
        status=doc_kwargs.pop("status", DocStatus.ready),
        **doc_kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    for index, spec in enumerate(chunk_specs):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=index,
                page_number=spec.get("page_number"),
                source=spec.get("source", ChunkSource.content),
                content=spec["content"],
                embedding=spec.get("embedding"),
            )
        )
    session.commit()
    return doc


_ODT_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2"><office:body><office:text><text:p>{text}</text:p></office:text></office:body></office:document-content>"""

_ODT_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>"""


def make_docx(path: Path, pages: list[str]) -> Path:
    """A .docx with one paragraph per entry and a hard page break between entries."""
    import docx

    document = docx.Document()
    for index, text in enumerate(pages):
        if index:
            document.add_page_break()
        document.add_paragraph(text)
    document.save(path)
    return path


def make_odt(path: Path, text: str) -> Path:
    """A minimal single-paragraph OpenDocument text file (mimetype entry first, stored)."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("content.xml", _ODT_CONTENT.format(text=text), compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", _ODT_MANIFEST, compress_type=zipfile.ZIP_DEFLATED)
    return path
