"""Load seed cases into the linking tables.

    export OFFSAID_HASH_KEY=<the SAME secret the API uses>
    export DATABASE_URL=postgresql://...
    python make_seed_data.py seed_data          # simulated demo cases (only once)
    python seed_linking.py seed_data/seed_cases.json

Works for simulated cases AND for real seeds from KEFWA or public warnings, in the same JSON format
(see "Real seeds" in the guide). Re-running is safe: rows are keyed on external_ref.
If OFFSAID_HASH_KEY ever changes, the stored hashes stop matching: wipe the tables and seed again.
"""
import json
import os
import sys
from pathlib import Path

import psycopg

from case_linking import CaseInput, get_hash_key, image_phash, record_case


def load_seed_file(conn, path: str) -> int:
    base = Path(path).parent
    key = get_hash_key()
    n = 0
    for c in json.loads(Path(path).read_text())["cases"]:
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
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        print("seeded", load_seed_file(conn, sys.argv[1]), "cases")
