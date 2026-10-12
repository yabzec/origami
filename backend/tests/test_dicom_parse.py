from datetime import date

import pydicom
import pytest
from PIL import Image

from app.services import dicom_parse
from app.services.dicom_parse import (
    JPEG_COPY_WARNING,
    build_preview,
    clean_title,
    file_key,
    is_viewer_manual,
    parse_date,
    parse_readme,
    parse_study_htm,
    patient_display_name,
)
from tests.helpers import make_dicom, make_text_pdf

REPORT_TEXT = [
    "Referto: frattura pressoche saldata con callo osseo al terzo medio della tibia destra,",
    "stabilizzata con chiodo endomidollare e viti, senza segni di mobilizzazione.",
]
README = """Patient Name : ROSSI MARIO
--- Cover Data ---
Paziente: ROSSI MARIO
Data di nascita: 19800101
Sesso: M
Data studio: 
Descrizione studio: Rx gamba (tibia e perone) - 88.27.3 Destro/a
Modalita: DX
"""
STUDY_HTM = """<html><body><table>
<tr><td>Identificativo paziente:</td><td>P-77</td></tr>
<tr><td>Accession Number:</td><td>ACC-9</td></tr>
<tr><td>Data dell'esame:</td><td>28/08/2026</td></tr>
</table></body></html>"""


def make_disc(root, **dicom_tags):
    make_dicom(root / "DICOM/1/2/3/img2", series_number=2, instance_number=1, SeriesDescription="Lateral", **dicom_tags)
    make_dicom(root / "DICOM/1/2/3/img1", series_number=1, instance_number=1, **dicom_tags)
    (root / "README.TXT").write_text(README)
    (root / "IHE_PDI").mkdir()
    (root / "IHE_PDI/Study000.htm").write_text(STUDY_HTM)
    make_text_pdf(root / "REPORT/8499735.pdf", REPORT_TEXT)
    make_text_pdf(root / "help.pdf", ["Viewer manual"])
    return root


def test_helpers():
    assert clean_title("Rx gamba (tibia e perone) - 88.27.3 Destro/a") == "Rx gamba (tibia e perone) Destro/a"
    assert clean_title("Rx torace") == "Rx torace"
    assert patient_display_name("ROSSI^MARIO") == "Mario Rossi"
    assert patient_display_name("ROSSI MARIO") == "Rossi Mario"
    assert parse_date("20260828") == date(2026, 8, 28)
    assert parse_date("28/08/2026") == date(2026, 8, 28)
    assert parse_date("2026-08-28") == date(2026, 8, 28)
    assert parse_date("20261345") is None
    assert parse_date("") is None
    assert is_viewer_manual("help.pdf")
    assert is_viewer_manual("VIEWERS/lang/guide.pdf")
    assert not is_viewer_manual("REPORT/8499735.pdf")
    assert len(file_key("DICOM/a")) == 16


def test_vendor_parsers():
    readme = parse_readme(README)
    assert readme["patient_name"] == "ROSSI MARIO"
    assert readme["study_description"].startswith("Rx gamba")
    assert "study_date" not in readme  # empty value is skipped
    assert parse_study_htm(STUDY_HTM) == {
        "patient_id": "P-77", "accession_number": "ACC-9", "study_date": "28/08/2026",
    }
    assert parse_readme("garbage without keys") == {}


def test_standard_disc_preview(tmp_path):
    preview = build_preview(make_disc(tmp_path))
    assert preview["no_study"] is False
    assert preview["warnings"] == []
    [study] = preview["studies"]
    assert study["title"] == "Rx gamba (tibia e perone) Destro/a — 2026-08-28"
    assert study["document_date"] == "2026-08-28"
    assert study["description_fallback"] == "Rx gamba (tibia e perone) Destro/a · Ospedale Test"
    assert [i["label"] for i in study["images"]] == ["AP", "Lateral"]  # series order, not file order
    assert all(i["readable"] and i["kind"] == "dicom" for i in study["images"])
    props = {p["key"]: p["value"] for p in study["properties"]}
    assert props["Patient ID"] == "P-1"  # DICOM wins over the htm value
    assert props["Birth date"] == "1980-01-01"
    assert props["Series"] == "AP (1); Lateral (1)"
    assert [p["key"] for p in study["properties"]][-1] == "Series"
    [report] = preview["reports"]  # help.pdf is excluded
    assert report["path"] == "REPORT/8499735.pdf"
    assert report["has_text"] and report["preselected"] and report["page_count"] == 1
    assert report["study_key"] == study["key"]


def test_vendor_files_fill_missing_tags(tmp_path):
    preview = build_preview(make_disc(tmp_path, StudyDate=None, PatientID=None, StudyDescription=None))
    [study] = preview["studies"]
    assert study["document_date"] == "2026-08-28"  # from Study000.htm
    assert {p["key"]: p["value"] for p in study["properties"]}["Patient ID"] == "P-77"
    assert study["title"].startswith("Rx gamba (tibia e perone) Destro/a")  # from README


def test_two_studies_keep_reports_unassigned(tmp_path):
    make_dicom(tmp_path / "DICOM/a", study_uid="1.2.826.0.1.1")
    make_dicom(tmp_path / "DICOM/b", study_uid="1.2.826.0.1.2", StudyDescription="Rx torace")
    make_text_pdf(tmp_path / "REPORT/r.pdf", REPORT_TEXT)
    preview = build_preview(tmp_path)
    assert len(preview["studies"]) == 2
    assert preview["reports"][0]["study_key"] is None


def test_undecodable_study_uses_jpeg_copies(tmp_path, monkeypatch):
    make_disc(tmp_path)
    for name in ("img1", "img2"):
        target = tmp_path / f"IHE_PDI/1/2/3/{name}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 30)).save(target)
    Image.new("RGB", (5, 5)).save(tmp_path / "IHE_PDI/Left.jpg")  # page decoration, ignored
    monkeypatch.setattr(dicom_parse, "is_decodable", lambda uid: False)
    preview = build_preview(tmp_path)
    [study] = preview["studies"]
    assert [i["path"] for i in study["images"]] == ["IHE_PDI/1/2/3/img1.jpg", "IHE_PDI/1/2/3/img2.jpg"]
    assert all(i["kind"] == "image" and i["readable"] for i in study["images"])
    assert preview["warnings"] == [JPEG_COPY_WARNING]


def test_undecodable_without_copies_is_marked_unreadable(tmp_path, monkeypatch):
    make_dicom(tmp_path / "DICOM/a")
    monkeypatch.setattr(dicom_parse, "is_decodable", lambda uid: False)
    [study] = build_preview(tmp_path)["studies"]
    assert study["images"][0]["readable"] is False


def test_no_dicom_is_no_study(tmp_path):
    (tmp_path / "README.TXT").write_text(README)
    preview = build_preview(tmp_path)
    assert preview["no_study"] is True
    assert preview["studies"] == []


def test_manual_files_form_a_study(tmp_path):
    Image.new("RGB", (20, 30)).save(tmp_path / "manual-b.png")
    (tmp_path / "manual").mkdir()
    Image.new("RGB", (20, 30)).save(tmp_path / "manual/b.png")
    Image.new("RGB", (20, 30)).save(tmp_path / "manual/a.jpg")
    make_text_pdf(tmp_path / "manual/referto.pdf", [])
    preview = build_preview(tmp_path)
    [study] = preview["studies"]
    assert study["key"] == "manual"
    assert [i["path"] for i in study["images"]] == ["manual/a.jpg", "manual/b.png"]
    assert study["title"] == "X-ray"
    assert study["document_date"] is None
    [report] = preview["reports"]
    assert report["preselected"] and report["has_text"] is False
    assert report["study_key"] == "manual"


def test_manual_images_join_the_only_study(tmp_path):
    make_dicom(tmp_path / "DICOM/a")
    (tmp_path / "manual").mkdir()
    Image.new("RGB", (20, 30)).save(tmp_path / "manual/extra.jpg")
    [study] = build_preview(tmp_path)["studies"]
    assert [i["path"] for i in study["images"]] == ["DICOM/a", "manual/extra.jpg"]


def test_encapsulated_pdf_becomes_report(tmp_path):
    pdf = make_text_pdf(tmp_path / "src.pdf", REPORT_TEXT).read_bytes()
    (tmp_path / "src.pdf").unlink()
    make_dicom(tmp_path / "DICOM/a")
    make_dicom(
        tmp_path / "DICOM/r",
        SOPClassUID="1.2.840.10008.5.1.4.1.1.104.1",
        EncapsulatedDocument=pdf,
        MIMETypeOfEncapsulatedDocument="application/pdf",
    )
    preview = build_preview(tmp_path)
    [report] = preview["reports"]
    assert report["path"].startswith(".extracted/")
    assert report["preselected"] and report["has_text"]
    assert len(preview["studies"][0]["images"]) == 1  # the PDF object is not an image


def test_broken_dicom_is_a_warning(tmp_path):
    (tmp_path / "DICOM").mkdir()
    (tmp_path / "DICOM/bad").write_bytes(b"\0" * 128 + b"DICM" + b"\xff" * 10)
    preview = build_preview(tmp_path)
    assert preview["no_study"] is True
    assert preview["warnings"] == ["DICOM/bad: not a readable DICOM file"]


def test_missing_transfer_syntax_is_unreadable(tmp_path):
    path = tmp_path / "DICOM/a"
    make_dicom(path)
    ds = pydicom.dcmread(path)
    del ds.file_meta.TransferSyntaxUID
    ds.save_as(path, enforce_file_format=False)
    [study] = build_preview(tmp_path)["studies"]
    assert study["images"][0]["readable"] is False


def test_failure_on_one_dicom_is_a_warning(tmp_path, monkeypatch):
    make_dicom(tmp_path / "DICOM/a")
    make_dicom(tmp_path / "DICOM/r", SOPClassUID="1.2.840.10008.5.1.4.1.1.104.1")

    def boom(*args):
        raise OSError("disk full")

    monkeypatch.setattr(dicom_parse, "_extract_encapsulated", boom)
    preview = build_preview(tmp_path)
    assert preview["warnings"] == ["DICOM/r: not a readable DICOM file"]
    assert len(preview["studies"][0]["images"]) == 1

