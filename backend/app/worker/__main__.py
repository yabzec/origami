import app.worker.pipeline  # noqa: F401  (registers handlers)
from app.db import engine
from app.worker.pipeline import ensure_sweep_scheduled
from app.worker.runner import main_loop

ensure_sweep_scheduled(engine)
main_loop(engine)
