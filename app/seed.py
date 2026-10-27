from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import LicensedAgent
from app.normalize import normalize_licence, normalize_name, normalize_phone


def load_agents_from_csv(db: Session, csv_path: str | Path | None = None) -> int:
    path = Path(csv_path or settings.seed_csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Seed CSV not found: {path}")

    added = 0
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            licence = normalize_licence(row["licence_number"])
            exists = (
                db.query(LicensedAgent)
                .filter(LicensedAgent.licence_number == licence)
                .first()
            )
            if exists:
                continue
            phone_raw = (row.get("phone") or "").strip()
            agent = LicensedAgent(
                name=row["name"].strip(),
                normalized_name=normalize_name(row["name"]),
                country=row["country"].strip(),
                licence_number=licence,
                phone=normalize_phone(phone_raw) or None,
                licence_status=row["licence_status"].strip(),
                source=row["source"].strip(),
            )
            db.add(agent)
            added += 1
    if added:
        db.commit()
    return added


def seed_if_empty(db: Session) -> int:
    if db.query(LicensedAgent).count() > 0:
        return 0
    return load_agents_from_csv(db)
