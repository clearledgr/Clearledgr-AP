#!/bin/sh
set -eu

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8010}"
WORKERS="${WORKERS:-4}"
LOG_LEVEL="${LOG_LEVEL:-info}"
export CLEARLEDGR_PROCESS_ROLE="${CLEARLEDGR_PROCESS_ROLE:-web}"

exec gunicorn main:app \
  --workers "${WORKERS}" \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind "${HOST}:${PORT}" \
  --access-logfile - \
  --error-logfile - \
  --log-level "${LOG_LEVEL}" \
  --timeout "${GUNICORN_TIMEOUT:-90}" \
  --graceful-timeout 30
  # --timeout 90: cold workers run database.initialize() lazily on
  # the first request, which can run ~50 IF NOT EXISTS DDL statements
  # against overlapping tables. The default 30s gunicorn timeout
  # SIGABRTs workers mid-init, leaving the api in a restart loop after
  # every redeploy. 90s gives schema init enough headroom while still
  # killing genuinely-stuck requests.
  # --graceful-timeout 30: when reloading, give workers 30s to finish
  # in-flight requests before SIGTERM.
