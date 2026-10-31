#!/usr/bin/env bash
# Create the OffsAid role and database on the local PostgreSQL server.
# Safe to re-run: existing role/database are left alone.
#   OFFSAID_DB_PASSWORD=... bash scripts/setup_postgres.sh
set -euo pipefail

DB_NAME="${OFFSAID_DB_NAME:-offsaid}"
DB_USER="${OFFSAID_DB_USER:-offsaid}"
: "${OFFSAID_DB_PASSWORD:?Set OFFSAID_DB_PASSWORD to the password for the $DB_USER role}"

sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -v db="$DB_NAME" -v user="$DB_USER" -v pass="$OFFSAID_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'user', :'pass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SQL

echo "Done. Put this in .env:"
echo "DATABASE_URL=postgresql+psycopg://${DB_USER}:<password>@localhost:5432/${DB_NAME}"
