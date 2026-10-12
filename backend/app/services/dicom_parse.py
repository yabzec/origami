"""Find the studies, images and reports in a staged disc (or loose files) and propose metadata."""

import hashlib
import logging
import re
from datetime import date
from html import unescape
from pathlib import Path, PurePosixPath

import pydicom

from app.services.dicom_render import is_decodable
from app.services.extract import extract_pdf_text, pdf_needs_ocr

log = logging.getLogger("origami.disc")

ENCAPSULATED_PDF = "1.2.840.10008.5.1.4.1.1.104.1"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MANUAL_DIR = "manual"
EXTRACTED_DIR = ".extracted"
REPORT_FILENAME = "Report.pdf"
VIEWER_MANUAL_WORDS = ("help", "manual", "guida")
PROCEDURE_CODE = re.compile(r"\s*-?\s*\b\d{2}\.\d{2}(?:\.\d+)?\b")
JPEG_COPY_WARNING = "The original images could not be decoded: the reduced copies from the disc are used."

README_KEYS = {
    "paziente": "patient_name",
    "patient name": "patient_name",
    "data di nascita": "birth_date",
    "sesso": "sex",
    "data studio": "study_date",
    "descrizione studio": "study_description",
    "modalita": "modality",
    "modalità": "modality",
}
HTM_KEYS = {
    "identificativo paziente": "patient_id",
    "accession number": "accession_number",
    "data dell'esame": "study_date",
}
FIELD_KEYS = (
    "patient_name", "birth_date", "sex", "patient_id", "accession_number", "institution",
    "modality", "body_part", "study_description", "device", "study_date",
)


def file_key(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:16]


def is_dicom(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except OSError:
        return False


def clean_title(description: str) -> str:
    """Drop procedure codes: `Rx gamba - 88.27.3 Destro/a` → `Rx gamba Destro/a`."""
    return " ".join(PROCEDURE_CODE.sub(" ", description).split())


def patient_display_name(raw: str) -> str:
    """DICOM `ROSSI^MARIO` → `Mario Rossi`; other formats keep their order, capitalized."""
    raw = raw.strip()
    if "^" in raw:
        family, _, rest = raw.partition("^")
        raw = f"{rest.split('^')[0]} {family}"
    return " ".join(part.capitalize() for part in raw.split())


def parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    for pattern, order in (
        (r"(\d{4})(\d{2})(\d{2})", "ymd"),
        (r"(\d{4})-(\d{2})-(\d{2})", "ymd"),
        (r"(\d{2})/(\d{2})/(\d{4})", "dmy"),
    ):
        match = re.fullmatch(pattern, text)
        if match:
            a, b, c = (int(g) for g in match.groups())
            try:
                return date(a, b, c) if order == "ymd" else date(c, b, a)
            except ValueError:
                return None
    return None


def parse_readme(text: str) -> dict[str, str]:
    """`Key: value` lines of a vendor README (best effort; unknown formats give {})."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        field = README_KEYS.get(key.strip().lower())
        if sep and field and value.strip() and field not in out:
            out[field] = value.strip()
    return out


def parse_study_htm(html: str) -> dict[str, str]:
    """`Label:value` pairs of an IHE PDI study page; the value may sit in the next element."""
    text = unescape(re.sub(r"<[^>]+>", "\n", html))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out: dict[str, str] = {}
    for index, line in enumerate(lines):
        key, sep, value = line.partition(":")
        field = HTM_KEYS.get(key.strip().lower())
        if not sep or not field or field in out:
            continue
        value = value.strip()
        if not value and index + 1 < len(lines) and ":" not in lines[index + 1]:
            value = lines[index + 1]
        if value:
            out[field] = value
    return out


def is_viewer_manual(rel: str) -> bool:
    path = PurePosixPath(rel.lower())
    if "viewers" in path.parts[:-1]:
        return True
    return len(path.parts) == 1 and any(word in path.stem for word in VIEWER_MANUAL_WORDS)


def _text(ds, keyword: str) -> str:
    return str(ds.get(keyword) or "").strip()


def _first_date(ds) -> date | None:
    for keyword in ("StudyDate", "SeriesDate", "ContentDate", "AcquisitionDate"):
        value = parse_date(ds.get(keyword))
        if value:
            return value
    return None


def _study_fields(ds) -> dict:
    birth = parse_date(ds.get("PatientBirthDate"))
    first = _first_date(ds)
    return {
        "patient_name": patient_display_name(_text(ds, "PatientName")),
        "birth_date": birth.isoformat() if birth else "",
        "sex": _text(ds, "PatientSex"),
        "patient_id": _text(ds, "PatientID"),
        "accession_number": _text(ds, "AccessionNumber"),
        "institution": _text(ds, "InstitutionName"),
        "modality": _text(ds, "Modality"),
        "body_part": _text(ds, "BodyPartExamined"),
        "study_description": _text(ds, "StudyDescription"),
        "device": " ".join(filter(None, [_text(ds, "Manufacturer"), _text(ds, "ManufacturerModelName")])),
        "study_date": first.isoformat() if first else "",
    }


def _image_label(ds, rel: str) -> str:
    series, view = _text(ds, "SeriesDescription"), _text(ds, "ViewPosition")
    if view and series and (view.casefold() in series.casefold() or series.casefold() in view.casefold()):
        view = ""  # "Lateral" and "LATERALE" say the same thing
    return " · ".join(filter(None, [series, view])) or PurePosixPath(rel).name


def _report(root: Path, rel: str, *, study_key: str | None, preselected: bool) -> dict | None:
    try:
        pages = extract_pdf_text(root / rel)
    except Exception:
        log.warning("Unreadable PDF %s", rel, exc_info=True)
        return None
    return {
        "key": file_key(rel),
        "path": rel,
        "filename": PurePosixPath(rel).name,
        "page_count": len(pages),
        "has_text": not pdf_needs_ocr(pages),
        "study_key": study_key,
        "preselected": preselected,
    }


def _extract_encapsulated(root: Path, path: Path, rel: str) -> str | None:
    """Write the PDF inside an Encapsulated PDF object to .extracted/ (once) and return its path."""
    target_rel = f"{EXTRACTED_DIR}/{file_key(rel)}.pdf"
    target = root / target_rel
    if not target.exists():
        data = pydicom.dcmread(path).get("EncapsulatedDocument")
        if not data:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))
    return target_rel


def _fill_from_vendor(fields: dict, readme: dict, htm: dict) -> None:
    for key, value in {**htm, **readme}.items():
        if fields.get(key):
            continue  # DICOM tags win
        if key in ("birth_date", "study_date"):
            parsed = parse_date(value)
            fields[key] = parsed.isoformat() if parsed else ""
        elif key == "patient_name":
            fields[key] = patient_display_name(value)
        else:
            fields[key] = value


def _series(images: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for image in images:
        counts[image["label"]] = counts.get(image["label"], 0) + image["frames"]
    return [{"description": label, "images": n} for label, n in counts.items()]


def _finish_study(key: str, fields: dict, images: list[dict]) -> dict:
    first_series = images[0]["label"] if images and images[0]["kind"] == "dicom" else ""
    description = clean_title(fields["study_description"])
    base = description or clean_title(first_series) or f"X-ray {fields['modality']}".strip()
    extra = {k: v for k, v in fields.items() if v and k != "study_date"}
    if description:
        extra["study_description"] = description
    extra["series"] = _series(images)
    return {
        "key": key,
        "title": f"{base} — {fields['study_date']}" if fields["study_date"] else base,
        "document_date": fields["study_date"] or None,
        "description_fallback": " · ".join(filter(None, [description, fields["institution"]])),
        "extra_metadata": extra,
        "images": images,
    }


def build_preview(root: Path) -> dict:
    """Studies (one per StudyInstanceUID), report candidates and warnings for a staging dir."""
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and not p.relative_to(root).parts[0].startswith(".")
    )
    groups: dict[str, list[tuple[tuple, dict, object]]] = {}
    reports: list[dict] = []
    warnings: list[str] = []
    jpeg_copies: dict[str, str] = {}
    loose: list[str] = []
    readme: dict = {}
    htm: dict = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        parts = PurePosixPath(rel.lower()).parts
        suffix = path.suffix.lower()
        if is_dicom(path):
            try:
                ds = pydicom.dcmread(path, stop_before_pixels=True)
                uid = _text(ds, "StudyInstanceUID") or "unknown"
                if _text(ds, "SOPClassUID") == ENCAPSULATED_PDF:
                    pdf_rel = _extract_encapsulated(root, path, rel)
                    report = pdf_rel and _report(root, pdf_rel, study_key=file_key(uid), preselected=True)
                    if report:
                        reports.append(report)
                    continue
                if "Rows" not in ds:
                    continue  # structured report, presentation state, ...: no pixels
                syntax = getattr(ds.file_meta, "TransferSyntaxUID", None)
                image = {
                    "key": file_key(rel),
                    "path": rel,
                    "kind": "dicom",
                    "label": _image_label(ds, rel),
                    "readable": bool(syntax) and is_decodable(str(syntax)),
                    "frames": int(ds.get("NumberOfFrames") or 1),
                }
                order = (int(ds.get("SeriesNumber") or 0), int(ds.get("InstanceNumber") or 0), rel)
            except Exception:
                log.warning("Unreadable DICOM %s", rel, exc_info=True)
                warnings.append(f"{rel}: not a readable DICOM file")
                continue
            groups.setdefault(uid, []).append((order, image, ds))
        elif parts == ("readme.txt",):
            readme = parse_readme(path.read_text(errors="replace"))
        elif parts[-1] == "study000.htm":
            htm = parse_study_htm(path.read_text(errors="replace"))
        elif suffix == ".pdf":
            if not is_viewer_manual(rel):
                preselected = "report" in parts[:-1] or parts[0] == MANUAL_DIR
                report = _report(root, rel, study_key=None, preselected=preselected)
                if report:
                    reports.append(report)
        elif suffix in IMAGE_EXTENSIONS:
            if parts[0] == MANUAL_DIR:
                loose.append(rel)
            elif parts[0] == "ihe_pdi" and len(parts) > 2:
                jpeg_copies["/".join(parts[1:])[: -len(suffix)]] = rel  # IHE_PDI/<rest>.jpg ↔ DICOM/<rest>

    studies: list[tuple[str, dict, list[dict]]] = []
    for uid, entries in groups.items():
        entries.sort(key=lambda e: e[0])
        images = [image for _, image, _ in entries]
        if not any(image["readable"] for image in images):
            copies = [jpeg_copies.get("/".join(PurePosixPath(i["path"].lower()).parts[1:])) for i in images]
            if any(copies):
                images = [
                    {**i, "path": c, "kind": "image", "readable": True, "frames": 1, "key": file_key(c)}
                    for i, c in zip(images, copies)
                    if c
                ]
                warnings.append(JPEG_COPY_WARNING)
        studies.append((file_key(uid), _study_fields(entries[0][2]), images))

    loose_images = [
        {"key": file_key(rel), "path": rel, "kind": "image", "label": PurePosixPath(rel).name,
         "readable": True, "frames": 1}
        for rel in loose
    ]
    if loose_images:
        if len(studies) == 1:
            studies[0][2].extend(loose_images)
        else:
            studies.append(("manual", {key: "" for key in FIELD_KEYS}, loose_images))

    if len(studies) == 1:
        _fill_from_vendor(studies[0][1], readme, htm)
        for report in reports:
            report["study_key"] = studies[0][0]

    return {
        "no_study": not studies,
        "studies": [_finish_study(key, fields, images) for key, fields, images in studies],
        "reports": reports,
        "warnings": list(dict.fromkeys(warnings)),
    }
