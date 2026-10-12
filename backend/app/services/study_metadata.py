"""Labels and plain text for the study data of imported X-ray discs (cover page, search, summary)."""

from datetime import date

# Same keys and labels as STUDY_FIELDS in frontend/src/lib/documentView.ts.
FIELDS: list[tuple[str, str]] = [
    ("patient_name", "Patient"),
    ("birth_date", "Birth date"),
    ("sex", "Sex"),
    ("patient_id", "Patient ID"),
    ("study_description", "Study"),
    ("modality", "Modality"),
    ("body_part", "Body part"),
    ("institution", "Institution"),
    ("accession_number", "Accession number"),
    ("device", "Device"),
]


def metadata_rows(meta: dict) -> list[tuple[str, str]]:
    rows = [(label, str(meta[key])) for key, label in FIELDS if meta.get(key)]
    series = meta.get("series") or []
    if series:
        rows.append(("Series", "; ".join(f"{s['description']} ({s['images']})" for s in series)))
    return rows


def metadata_text(meta: dict) -> str:
    return "\n".join(f"{label}: {value}" for label, value in metadata_rows(meta))


def cover_rows(meta: dict, study_date: date) -> list[tuple[str, str]]:
    return [("Study date", study_date.isoformat()), *metadata_rows(meta)]
