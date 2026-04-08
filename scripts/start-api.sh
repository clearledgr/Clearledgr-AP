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
  --log-level "${LOG_LEVEL}"
