"""Enqueue a process_document job for every pending document.

One-shot companion to the vector-dimension migration, which resets documents to
`pending` without enqueuing work (Alembic must not depend on the worker). Safe to
re-run: it only ever looks at documents already in `pending`.

Usage, from backend/:  uv run python -m scripts.reingest_pending
"""

import logging

from sqlmodel import Session, create_engine, select

from app.config import get_settings
from app.models import DocStatus, Document
from app.services.jobs import enqueue

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("origami.reingest")


def main() -> None:
    engine = create_engine(get_settings().database_url)
    with Session(engine) as session:
        pending = session.exec(
            select(Document).where(Document.status == DocStatus.pending)
        ).all()
        log.info("found %d pending documents", len(pending))
        for doc in pending:
            # `enqueue` commits per job, so an interrupted run leaves the jobs it
            # already created — re-running only picks up what is still pending.
            enqueue(session, "process_document", {"document_id": str(doc.id)})
            log.info("enqueued %s (%s)", doc.id, doc.title)
    log.info("done; the worker will process them")


if __name__ == "__main__":
    main()
