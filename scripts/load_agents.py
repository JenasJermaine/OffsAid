"""Load (or reload missing rows from) the simulated licensed-agent CSV."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.seed import load_agents_from_csv  # noqa: E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        added = load_agents_from_csv(db)
        print(f"Added {added} licensed agent row(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
