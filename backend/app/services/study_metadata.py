"""Labels and plain text for document properties (X-ray study data, cover page, search, summary)."""

from datetime import date

# Labels of the study fields read from an X-ray disc, in display order.
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


def study_properties(fields: dict, series: list[dict]) -> list[dict[str, str]]:
    rows = [{"key": label, "value": str(fields[key])} for key, label in FIELDS if fields.get(key)]
    if series:
        rows.append({"key": "Series", "value": "; ".join(f"{s['description']} ({s['images']})" for s in series)})
    return rows


def properties_text(props: list[dict]) -> str:
    return "\n".join(f"{p['key']}: {p['value']}" for p in props)


def cover_rows(props: list[dict], study_date: date) -> list[tuple[str, str]]:
    return [("Study date", study_date.isoformat()), *((p["key"], p["value"]) for p in props)]
