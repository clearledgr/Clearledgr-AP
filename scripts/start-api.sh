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
  # --timeout 90: the Gmail push handler now enqueues to Celery via
  # process_gmail_push.delay() and returns 200 immediately, so the
  # web worker no longer runs the LLM-classification pipeline inline.
  # 90s is enough headroom for any genuinely-slow request (cold-start
  # init under the advisory lock, large workspace bootstrap query)
  # while still killing truly stuck requests.
  # --graceful-timeout 30: when reloading, give workers 30s to finish
  # in-flight requests before SIGTERM.
