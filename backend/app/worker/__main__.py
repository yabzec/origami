import app.worker.pipeline  # noqa: F401  (registers handlers)
from app.db import engine as default_engine
from app.services.embedding_check import startup_guard
from app.worker.pipeline import ensure_sweep_scheduled
from app.worker.runner import main_loop


def main(engine=default_engine) -> None:
    startup_guard(engine)  # refuse to embed with a model that doesn't match the stored vectors
    ensure_sweep_scheduled(engine)
    main_loop(engine)


if __name__ == "__main__":
    main()
