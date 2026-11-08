#!/bin/sh
# Container start command (Dockerfile CMD). On Postgres: apply migrations/*.sql, optionally load the
# simulated cross-case linking cases, then start one Uvicorn worker. A migration or seed failure is
# logged but does not stop the app: the report then says the comparison was not available.
set -u

case "${DATABASE_URL:-}" in
  postgres*)
    python -m scripts.migrate || echo "WARNING: migrations failed; cross-case linking will be unavailable" >&2
    if [ "${SEED_LINKING_DEMO:-false}" = "true" ]; then
      python -m scripts.seed_linking data/linking_seed/seed_cases.json \
        || echo "WARNING: loading the simulated linking cases failed" >&2
    fi
    ;;
esac

# One worker: chat state, the linking rate limit and the startup seed are per process.
# --proxy-headers so the Twilio signature check sees Render's https URL, not the internal http one.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}" --workers 1 \
  --proxy-headers --forwarded-allow-ips='*'
