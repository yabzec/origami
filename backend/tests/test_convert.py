from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.convert import OFFICE_EXTENSIONS, ConversionError, office_to_pdf
from tests.helpers import make_docx, make_odt


def test_office_extensions():
    assert OFFICE_EXTENSIONS == {".doc", ".docx", ".odt", ".rtf"}


def test_docx_converts_to_pdf(tmp_path):
    pdf = office_to_pdf(make_docx(tmp_path / "a.docx", ["CONTRATTO DI LOCAZIONE"]))
    assert pdf.startswith(b"%PDF")


def test_odt_converts_to_pdf(tmp_path):
    pdf = office_to_pdf(make_odt(tmp_path / "a.odt", "VERBALE ASSEMBLEA"))
    assert pdf.startswith(b"%PDF")


def test_corrupt_file_raises(tmp_path):
    good = make_docx(tmp_path / "good.docx", ["testo"])
    bad = tmp_path / "bad.docx"
    bad.write_bytes(good.read_bytes()[:300])  # truncated zip: LibreOffice cannot load it
    with pytest.raises(ConversionError, match="Office conversion failed"):
        office_to_pdf(bad)


def test_failing_binary_raises(tmp_path, break_soffice):
    break_soffice()  # /bin/false exits 1
    with pytest.raises(ConversionError, match=r"exit 1"):
        office_to_pdf(make_docx(tmp_path / "a.docx", ["testo"]))


def test_missing_binary_raises(tmp_path, break_soffice):
    break_soffice("/nonexistent/soffice")
    with pytest.raises(ConversionError, match="could not start"):
        office_to_pdf(make_docx(tmp_path / "a.docx", ["testo"]))


def test_timeout_raises(tmp_path, break_soffice):
    slow = tmp_path / "slow-soffice"
    slow.write_text("#!/bin/sh\nsleep 10\n")
    slow.chmod(0o755)
    break_soffice(str(slow))
    with pytest.raises(ConversionError, match="timed out"):
        office_to_pdf(make_docx(tmp_path / "a.docx", ["testo"]), timeout=0.5)


def test_concurrent_conversions_do_not_collide(tmp_path):
    sources = [make_docx(tmp_path / f"d{i}.docx", [f"DOCUMENTO {i}"]) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(office_to_pdf, sources))
    assert all(pdf.startswith(b"%PDF") for pdf in results)
