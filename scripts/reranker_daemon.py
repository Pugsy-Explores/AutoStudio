#!/usr/bin/env python3
"""Backward-compat wrapper: delegates to retrieval_daemon (reranker + embedding).

Use: python scripts/retrieval_daemon.py  (unified daemon)
This script is kept for backward compatibility with existing scripts/docs.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Delegate to retrieval_daemon; support --stop by mapping to retrieval daemon PID file
if "--stop" in sys.argv:
    from scripts.retrieval_daemon import _remove_pid

    PID_FILE = _ROOT / "logs" / "retrieval_daemon.pid"
    if PID_FILE.exists():
        import os

        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 15)
            _remove_pid()
            print(f"Sent SIGTERM to retrieval daemon PID {pid}")
        except (ProcessLookupError, ValueError):
            _remove_pid()
            print("Process already gone")
    else:
        print("No PID file — retrieval daemon not running?")
    sys.exit(0)

# Run retrieval daemon with same args
import runpy

runpy.run_path(str(_ROOT / "scripts" / "retrieval_daemon.py"), run_name="__main__")
