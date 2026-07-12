from app.db import engine
from app.worker.runner import main_loop

main_loop(engine)
