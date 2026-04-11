#!/bin/sh
set -eu

export CLEARLEDGR_PROCESS_ROLE="${CLEARLEDGR_PROCESS_ROLE:-worker}"

# §11.2.1: Celery worker fleet — stateless processes consuming from Redis Streams.
# Falls back to legacy async worker if Celery is not available.
if python3 -c "import celery" 2>/dev/null; then
    exec celery -A clearledgr.services.celery_app worker -l info -c "${CELERY_CONCURRENCY:-4}"
else
    echo "WARNING: celery not installed, falling back to legacy async worker"
    exec python3 -m clearledgr.services.worker_runtime
fi
