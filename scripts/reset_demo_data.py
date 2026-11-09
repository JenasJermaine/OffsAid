"""Empty everything players and testers created, so the database is back to the demo starting point.

    # PowerShell. The URL is the database's EXTERNAL Database URL from the Render dashboard.
    $env:DATABASE_URL = "postgresql://..."
    .\\.venv\\Scripts\\python.exe -m scripts.reset_demo_data          # shows what is there, changes nothing
    .\\.venv\\Scripts\\python.exe -m scripts.reset_demo_data --yes    # empties it
    Remove-Item Env:DATABASE_URL

Emptied: every case (live AND simulated), with its hashed identifiers and letter/wording fingerprints, the
KEFWA review queue (including players' phone numbers from referrals), web-check jobs and their logs, agent
lookup logs, and the record of processed WhatsApp message ids.
Kept: the licensed-agent list and the record of applied migrations.

The simulated cases are removed too, so their statuses go back to the seed values: redeploy the Render
service afterwards and its start script loads the 10 simulated cases again with the server's own
OFFSAID_HASH_KEY. (Or run scripts.seed_linking yourself with that same key.)

Only DATABASE_URL from the environment or --dsn is used, never .env, so this cannot reset a database by accident.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

TABLES = {
    # table: what it holds
    "cases": "cases (offers checked, plus the simulated seeds)",
    "identifiers": "hashed phone numbers, accounts, emails, domains",
    "documents": "letter and wording fingerprints",
    "review_queue": "KEFWA review queue (escalations and chat referrals)",
    "web_check_logs": "web-checking agent audit log",
    "web_checks": "web-checking agent jobs",
    "lookup_logs": "agent-check lookup log (hashed)",
    "inbound_messages": "processed WhatsApp message ids",
}


def plain_dsn(url: str) -> str:
    """postgresql+psycopg:// or postgres:// -> postgresql:// for psycopg."""
    return re.sub(r"^postgres(?:ql)?(?:\+\w+)?://", "postgresql://", url)


def counts(conn) -> dict:
    out = {}
    for table in TABLES:
        exists = conn.execute("select to_regclass(%s)", (f"public.{table}",)).fetchone()[0]
        out[table] = conn.execute(f"select count(*) from {table}").fetchone()[0] if exists else None
    return out


def show(conn) -> None:
    for table, n in counts(conn).items():
        print(f"  {table:<18} {'(missing)' if n is None else n:>8}   {TABLES[table]}")
    if counts(conn)["cases"]:
        rows = conn.execute("select source, count(*) from cases group by source order by source").fetchall()
        print("  cases by source:  " + ", ".join(f"{source} {n}" for source, n in rows))


def reset(conn) -> None:
    present = [t for t, n in counts(conn).items() if n is not None]
    # One statement, one transaction: identifiers, documents and review_queue reference cases, and
    # web_check_logs references web_checks, so they are emptied together.
    conn.execute(f"truncate table {', '.join(present)} restart identity")


def main() -> int:
    import psycopg

    parser = argparse.ArgumentParser(description="Reset the OffsAid demo database (see the module docstring).")
    parser.add_argument("--dsn", help="Postgres URL; default: the DATABASE_URL environment variable")
    parser.add_argument("--yes", action="store_true", help="really empty the tables (without it, only show counts)")
    args = parser.parse_args()
    url = args.dsn or os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgres"):
        print("Set DATABASE_URL (or --dsn) to the Postgres URL. Only Postgres databases can be reset.", file=sys.stderr)
        return 2
    host = re.sub(r"^.*@", "", url).split("/", 1)[0]
    with psycopg.connect(plain_dsn(url)) as conn:
        print(f"Database at {host}:")
        show(conn)
        if not args.yes:
            print("\nNothing changed. Run again with --yes to empty these tables.")
            return 0
        with conn.transaction():
            reset(conn)
        print("\nEmptied. Now:")
        show(conn)
    print("\nNext: redeploy the Render service (Manual Deploy > Deploy latest commit). Its start script reloads the "
          "10 simulated cases; the deploy log shows 'seeded 10 cases'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
