from sqlmodel import Session, create_engine

from app.config import get_settings

engine = create_engine(get_settings().database_url)


def get_session():
    with Session(engine) as session:
        yield session
