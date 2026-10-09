import app.worker.pipeline  # noqa: F401  (registers handlers)
from app.db import engine
from app.worker.pipeline import ensure_sweep_scheduled
from app.worker.runner import main_loop

from app.services.storage import OldStorageLayout, get_storage, require_new_layout

try:
    require_new_layout(get_storage())
except OldStorageLayout as exc:
    raise SystemExit(str(exc))

ensure_sweep_scheduled(engine)
main_loop(engine)
