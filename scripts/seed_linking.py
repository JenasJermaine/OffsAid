"""Load seed cases into the linking tables.

    # OFFSAID_HASH_KEY and DATABASE_URL come from the environment or .env (the SAME key the API uses)
    python scripts/make_seed_data.py data/linking_seed        # simulated demo cases (already committed)
    python -m scripts.migrate                                 # once: create the linking tables
    python -m scripts.seed_linking data/linking_seed/seed_cases.json

Works for simulated cases AND for real seeds from KEFWA or public warnings, in the same JSON format
(see "Real seeds" in docs/CROSS_CASE_LINKING_GUIDE.md). Re-running is safe: rows are keyed on external_ref.
If OFFSAID_HASH_KEY ever changes, the stored hashes stop matching: wipe the tables and seed again.
"""
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.case_linking import CaseInput, get_hash_key, image_phash, record_case  # noqa: E402


def load_seed_file(conn, path: str, key: Optional[bytes] = None) -> int:
    base = Path(path).parent
    key = key or get_hash_key()
    n = 0
    for c in json.loads(Path(path).read_text(encoding="utf-8"))["cases"]:
        if c["source"] == "public_warning" and not c.get("source_url"):
            raise ValueError(f"{c.get('external_ref')}: public_warning rows need a source_url")
        phash = image_phash(base / c["letter_image"]) if c.get("letter_image") else None
        record_case(conn, CaseInput(
            source=c["source"], status=c.get("status", "unreviewed"), external_ref=c.get("external_ref"),
            claimed_club=c.get("claimed_club"), claimed_agent=c.get("claimed_agent"),
            identifiers=c.get("identifiers", []), letter_text=c.get("letter_text"), letter_phash=phash,
            wording_text=c.get("wording_text"), wording_labels=c.get("wording_labels", []),
            source_url=c.get("source_url"), summary=c.get("summary", {})), key)
        n += 1
    return n


if __name__ == "__main__":
    import psycopg

    from app.linking_step import hash_key, psycopg_dsn

    with psycopg.connect(psycopg_dsn(), autocommit=True) as conn:
        print("seeded", load_seed_file(conn, sys.argv[1], hash_key()), "cases")
