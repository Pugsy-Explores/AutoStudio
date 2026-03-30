#!/usr/bin/env bash
# Install deps then run tests. Use this to ensure tests always have required packages.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
"$ROOT/scripts/install_test_deps.sh"
exec "${PYTHON:-python3}" -m pytest "$@"
