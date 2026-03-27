from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import Engine, event

_engine: Engine | None = None

# Default DB path: <plugin_root>/data/lab.db
_DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def init_db(url: str | None = None) -> Engine:
    """Initialize the database engine and create all tables."""
    global _engine

    if url is None:
        _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
        db_path = _DEFAULT_DB_DIR / "lab.db"
        url = f"sqlite:///{db_path}"

    _engine = create_engine(url, echo=False)

    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    SQLModel.metadata.create_all(_engine)
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a SQLModel session. Commits on success, rolls back on error."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    with Session(_engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
