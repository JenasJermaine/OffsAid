from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url in {"sqlite://", "sqlite:///"}:
            kwargs["poolclass"] = StaticPool
        return kwargs
    return {"pool_pre_ping": True}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def init_db() -> None:
    """Create all SQLAlchemy tables, including the parked pipeline tables. Not called yet:
    Part 1 uses Base.metadata.create_all in main. The cross-case linking tables are
    plain-SQL Postgres migrations instead (python -m scripts.migrate)."""
    import app.models  # noqa: F401  registers tables on Base.metadata
    import app.pipeline_models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
