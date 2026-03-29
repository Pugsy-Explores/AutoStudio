#!/usr/bin/env bash
# Run retrieval daemon in background with logs (replacement for deprecated --daemon).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/logs"
nohup python "$ROOT/scripts/retrieval_daemon.py" "$@" \
  > "$ROOT/logs/daemon.log" 2>&1 &
echo "Retrieval daemon started (PID $!). Logs: $ROOT/logs/daemon.log"
