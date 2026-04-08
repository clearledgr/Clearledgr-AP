#!/bin/sh
set -eu

export CLEARLEDGR_PROCESS_ROLE="${CLEARLEDGR_PROCESS_ROLE:-worker}"

exec python3 -m clearledgr.services.worker_runtime
