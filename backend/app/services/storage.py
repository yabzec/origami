import shutil
import uuid
from pathlib import Path

from app.config import get_settings


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def files_dir(self) -> Path:
        d = self.root / "files"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def tmp_scans_dir(self) -> Path:
        d = self.root / "tmp" / "scan_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def abs_path(self, rel: str) -> Path:
        return self.root / rel

    def store_file(self, document_id: uuid.UUID, ext: str, data: bytes) -> tuple[str, int]:
        name = f"{document_id}{ext}"
        path = self.files_dir / name
        path.write_bytes(data)
        return f"files/{name}", len(data)

    def delete_document_file(self, rel: str | None) -> None:
        if rel:
            self.abs_path(rel).unlink(missing_ok=True)

    def scan_session_dir(self, session_id: int) -> Path:
        d = self.tmp_scans_dir / str(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_scan_session_dir(self, session_id: int) -> None:
        shutil.rmtree(self.tmp_scans_dir / str(session_id), ignore_errors=True)


def get_storage() -> Storage:
    return Storage(get_settings().storage_path)
