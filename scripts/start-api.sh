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
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --graceful-timeout 30
  # --timeout 180 (was 90): the Gmail-push BackgroundTasks pipeline
  # can chain LLM classification + invoice extraction for a batch of
  # new emails inline on the web worker. The most obvious sync-from-
  # async hotspot (classify_email_with_llm) is now wrapped in
  # asyncio.to_thread, but a burst of 5+ inbound emails still chews
  # through real time. 180s keeps workers alive through a realistic
  # burst while still killing truly stuck requests. The proper fix
  # is to enqueue the Gmail push payload to Redis and let the worker
  # service consume it — refactor for tomorrow, not tonight.
  # --graceful-timeout 30: when reloading, give workers 30s to finish
  # in-flight requests before SIGTERM.
