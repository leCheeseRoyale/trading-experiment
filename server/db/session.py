from pathlib import Path
from contextlib import contextmanager

from sqlmodel import SQLModel, Session, create_engine

_engine = None


def _default_db_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "lab.db"


def init_db(db_path: Path | None = None) -> None:
    global _engine
    if db_path is None:
        db_path = _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    _engine = create_engine(url, echo=False)

    with _engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text("PRAGMA journal_mode=WAL"))
        conn.execute(__import__("sqlalchemy").text("PRAGMA busy_timeout=5000"))
        conn.commit()

    SQLModel.metadata.create_all(_engine)


@contextmanager
def get_session():
    global _engine
    if _engine is None:
        init_db()
    with Session(_engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
