#!/usr/bin/env bash
# Install AutoStudio + everything needed to run the test suite.
# Always run this before pytest in fresh clones or CI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
echo "[install_test_deps] Installing dependencies..."
if [[ -f "pyproject.toml" ]]; then
  "$PY" -m pip install -e ".[test]" 2>/dev/null || "$PY" -m pip install -r requirements-dev.txt
else
  "$PY" -m pip install -r requirements-dev.txt
fi
# Ensure rank-bm25 and tree-sitter-python (conftest requires them)
"$PY" -m pip install "rank-bm25>=0.2.2" "tree-sitter-python>=0.20.0" --quiet 2>/dev/null || true
echo "[install_test_deps] Done. Run: $PY -m pytest tests/ -q"
