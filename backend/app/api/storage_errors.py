from contextlib import contextmanager

from app.api.deps import api_error


@contextmanager
def storage_errors():
    """Turn filesystem errors from the storage tree into API errors."""
    try:
        yield
    except FileExistsError as exc:
        raise api_error(
            409, "storage_conflict", f"A file or folder already exists at {exc.filename or exc}"
        ) from exc
    except OSError as exc:
        raise api_error(
            500, "storage_error", f"Storage error at {exc.filename}: {exc.strerror or exc}"
        ) from exc
