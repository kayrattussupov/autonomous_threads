import os
from collections.abc import Generator

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from src.db.engine import get_sessionmaker


def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ["API_BEARER_TOKEN"]
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def get_db() -> Generator[Session, None, None]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
