import os
import signal
import subprocess
import tempfile
from pathlib import Path

from app.config import get_settings

OFFICE_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf"}
CONVERT_TIMEOUT_SECONDS = 120.0
STDERR_LIMIT = 500


class ConversionError(Exception):
    """LibreOffice could not turn the file into a PDF."""


def office_to_pdf(src: Path, timeout: float = CONVERT_TIMEOUT_SECONDS) -> bytes:
    """Convert an office document to PDF bytes with headless LibreOffice.

    Every call uses its own temp dir and LibreOffice profile, so concurrent conversions
    never fight over the profile lock.
    """
    with tempfile.TemporaryDirectory(prefix="origami-soffice-", ignore_cleanup_errors=True) as tmp:
        workdir = Path(tmp)
        cmd = [
            get_settings().soffice_path,
            "--headless",
            "--norestore",
            f"-env:UserInstallation={(workdir / 'profile').as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workdir),
            str(src),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # own process group: a timeout kills soffice.bin too
            )
        except OSError as exc:
            raise ConversionError(f"Office conversion could not start: {exc}") from exc
        try:
            _, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise ConversionError(f"Office conversion timed out after {timeout:g}s") from None
        detail = (stderr or "").strip()[:STDERR_LIMIT]
        if proc.returncode != 0:
            raise ConversionError(f"Office conversion failed (exit {proc.returncode}): {detail}")
        output = workdir / f"{src.stem}.pdf"
        if not output.is_file():
            raise ConversionError(f"Office conversion produced no PDF: {detail}")
        return output.read_bytes()
