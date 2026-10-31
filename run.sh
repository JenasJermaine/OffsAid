#!/usr/bin/env bash
# Run the OffsAid API from the project venv (no Docker).
# Extra arguments go to uvicorn, e.g. ./run.sh --reload
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
    echo "No venv found. Run 'make install' first." >&2
    exit 1
fi

exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
