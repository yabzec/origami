import app.worker.pipeline  # noqa: F401  (registers the process_document handler)
from app.db import engine
from app.worker.runner import main_loop

main_loop(engine)
