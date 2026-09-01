import os

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://threads_agent:changeme@localhost:5432/threads_agent_test")

from src.db.engine import get_engine, get_sessionmaker
from src.db.models import Base


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    try:
        engine = get_engine()
        Base.metadata.create_all(engine)
        yield
        Base.metadata.drop_all(engine)
    except Exception:
        # Database not available, skip schema creation
        yield


@pytest.fixture()
def db_session():
    Session = get_sessionmaker()
    session = Session()
    try:
        yield session
        session.rollback()
    finally:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
        session.commit()
        session.close()
