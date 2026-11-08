"""Apply the plain-SQL migrations in migrations/ to the Postgres database, in file-name order.

    python -m scripts.migrate                 # uses DATABASE_URL from the environment or .env
    python -m scripts.migrate --dsn postgresql://...

Each file runs once, in its own transaction, and is recorded in schema_migrations.
These migrations are Postgres only (cross-case linking needs pg_trgm and bit_count, Postgres 14+).
The other tables are still created by SQLAlchemy (Base.metadata.create_all) at startup.
`create extension pg_trgm` needs the right privilege; on a managed database enable it in the dashboard first.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIGRATIONS = ROOT / "migrations"


def apply_migrations(conn, folder: Path = MIGRATIONS) -> list[str]:
    """conn: psycopg 3 connection in autocommit mode. Returns the names of the files applied now."""
    conn.execute("create table if not exists schema_migrations "
                 "(name text primary key, applied_at timestamptz not null default now())")
    done = {row[0] for row in conn.execute("select name from schema_migrations").fetchall()}
    applied = []
    for path in sorted(folder.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        applied.append(path.name)
    return applied


if __name__ == "__main__":
    import psycopg

    from app.linking_step import psycopg_dsn

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", help="Postgres DSN; default: DATABASE_URL")
    args = parser.parse_args()
    with psycopg.connect(args.dsn or psycopg_dsn(), autocommit=True) as conn:
        names = apply_migrations(conn)
    print("applied:", ", ".join(names) if names else "nothing (up to date)")
