"""Ensure retrieval daemon is reachable; start it if not running and auto-start is enabled.

If the daemon is already running (per config: health check on RETRIEVAL_DAEMON_PORT),
use that instance only — do not start a second one. Only when the daemon is not
reachable and RETRIEVAL_DAEMON_AUTO_START is on do we start it.

Used at controller entry so the main agent loop can use the daemon for embeddings
and remote retrieval when configured. Reranking is always in-process (MiniLM ONNX).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from config.retrieval_config import (
    EMBEDDING_USE_DAEMON,
    RETRIEVAL_DAEMON_AUTO_START,
    RETRIEVAL_DAEMON_PORT,
    RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_HEALTH_URL = "http://127.0.0.1"
_POLL_INTERVAL_SECONDS = 3


def _check_daemon_health(port: int = RETRIEVAL_DAEMON_PORT) -> tuple[bool, dict]:
    """GET /health; return (healthy_per_our_needs, data)."""
    url = f"{_HEALTH_URL}:{port}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return False, {}
            data = json.loads(resp.read().decode())
            need_embedding = EMBEDDING_USE_DAEMON
            emb_ok = bool(data.get("embedding_routing_ok")) or bool(data.get("embedding_loaded", False))
            healthy = not need_embedding or emb_ok
            return healthy, data
    except Exception as e:
        logger.debug("[daemon_ensure] health check failed: %s", e)
        return False, {}


def _start_daemon(project_root: str | Path) -> bool:
    """Start retrieval daemon in background. Returns True if subprocess was started."""
    root = Path(project_root).resolve()
    script = root / "scripts" / "retrieval_daemon.py"
    if not script.is_file():
        logger.warning("[daemon_ensure] daemon script not found at %s", script)
        return False
    try:
        subprocess.Popen(
            [sys.executable, str(script), "--daemon"],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("[daemon_ensure] started retrieval daemon (scripts/retrieval_daemon.py --daemon)")
        return True
    except Exception as e:
        logger.warning("[daemon_ensure] failed to start daemon: %s", e)
        return False


def ensure_retrieval_daemon(project_root: str | Path) -> bool:
    """Ensure retrieval daemon is reachable; start it if not running and auto-start is enabled.

    If the daemon is already running (per health check), use that only — do not start
    a second instance. Returns True if daemon is reachable (existing or after start),
    False otherwise.
    """
    if not EMBEDDING_USE_DAEMON:
        return False

    healthy, data = _check_daemon_health(RETRIEVAL_DAEMON_PORT)
    if healthy:
        logger.info(
            "[daemon_ensure] retrieval daemon already running (per config); using daemon only"
        )
        return True

    if not RETRIEVAL_DAEMON_AUTO_START:
        logger.debug("[daemon_ensure] auto-start disabled; daemon not reachable")
        return False

    if not _start_daemon(project_root):
        return False

    timeout = max(1, RETRIEVAL_DAEMON_START_TIMEOUT_SECONDS)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SECONDS)
        healthy, data = _check_daemon_health(RETRIEVAL_DAEMON_PORT)
        if healthy:
            logger.info(
                "[daemon_ensure] retrieval daemon ready (embedding=%s)",
                data.get("embedding_loaded", False),
            )
            return True

    logger.warning(
        "[daemon_ensure] retrieval daemon did not become healthy within %s s; "
        "agent will use in-process embedding fallback",
        timeout,
    )
    return False
