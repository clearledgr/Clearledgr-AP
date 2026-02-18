#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${ROOT_DIR}/.server.pid"
LOG_FILE="${ROOT_DIR}/server.log"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

load_env() {
  set +u
  set -a
  if [[ -f "${ROOT_DIR}/.env" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env"
  fi
  if [[ -f "${ROOT_DIR}/.env.local" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env.local"
  fi
  set +a
  set -u
}

running_pid() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    return 1
  fi
  if kill -0 "${pid}" 2>/dev/null; then
    echo "${pid}"
    return 0
  fi
  return 1
}

port_open() {
  HOST="${HOST}" PORT="${PORT}" "${PYTHON_BIN}" - <<'PY' >/dev/null 2>&1
import os
import socket

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", "8000"))
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.8)
try:
    s.connect((host, port))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
raise SystemExit(0)
PY
}

start() {
  if pid="$(running_pid)"; then
    echo "Backend supervisor already running (pid ${pid})."
    return 0
  fi

  cd "${ROOT_DIR}"
  nohup "${BASH_SOURCE[0]}" __run-loop >>"${LOG_FILE}" 2>&1 &

  local pid=$!
  echo "${pid}" > "${PID_FILE}"
  echo "Backend supervisor started (pid ${pid})."
  for _ in {1..20}; do
    if port_open; then
      status || true
      return 0
    fi
    sleep 0.5
  done

  echo "Backend failed to become reachable on ${HOST}:${PORT}."
  if [[ -f "${LOG_FILE}" ]]; then
    echo "--- recent backend logs ---"
    tail -n 40 "${LOG_FILE}" || true
    echo "--- end logs ---"
  fi
  return 1
}

run_loop() {
  cd "${ROOT_DIR}"
  load_env
  while true; do
    set +e
    "${PYTHON_BIN}" -m uvicorn main:app --host "${HOST}" --port "${PORT}"
    code=$?
    set -e
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backend exited with code ${code}, restarting in 2s"
    sleep 2
  done
}

stop() {
  if ! pid="$(running_pid)"; then
    echo "Backend supervisor is not running."
    rm -f "${PID_FILE}"
    return 0
  fi

  kill "${pid}" 2>/dev/null || true
  sleep 1
  if kill -0 "${pid}" 2>/dev/null; then
    kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
  echo "Backend supervisor stopped."
}

status() {
  if pid="$(running_pid)"; then
    echo "Backend supervisor running (pid ${pid})"
    return 0
  fi
  if port_open; then
    echo "Backend is reachable on ${HOST}:${PORT} but is not managed by dev-backend supervisor."
    return 0
  fi
  echo "Backend supervisor not running"
  return 1
}

logs() {
  touch "${LOG_FILE}"
  tail -f "${LOG_FILE}"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) logs ;;
  __run-loop) run_loop ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 1
    ;;
esac
