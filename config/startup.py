"""Startup bootstrap: ensure retrieval daemon is running, reranker ready, LLM endpoints reachable.

Principal-engineer contract:
- Retrieval daemon: if not running → auto-start it (reranker + embedding warm).
- Reranker: if daemon not used, init in-process before any work.
- LLM models: if unreachable → state error clearly and exit.

Call ensure_services_ready() before run_agent() or any agent entrypoint.

Set SKIP_STARTUP_CHECKS=1 to bypass (e.g. tests with mocked services).
Set RETRIEVAL_DAEMON_AUTO_START=0 to skip auto-starting the daemon.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT_SEC = 10
_DAEMON_START_TIMEOUT = 60
_DAEMON_POLL_INTERVAL = 1


def _retrieval_daemon_health(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if retrieval daemon /health returns 200 and reranker_loaded."""
    try:
        req = urllib.request.Request(f"http://{host}:{port}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return False
            import json

            data = json.loads(resp.read().decode())
            return data.get("reranker_loaded", False) or data.get("embedding_loaded", False)
    except Exception:
        return False


def _ensure_retrieval_daemon_running() -> None:
    """Ensure retrieval daemon is running. Auto-start if configured and not running."""
    from config.retrieval_config import RETRIEVAL_DAEMON_PORT

    if _retrieval_daemon_health(RETRIEVAL_DAEMON_PORT):
        logger.info("Retrieval daemon already running on port %s.", RETRIEVAL_DAEMON_PORT)
        return

    if os.getenv("RETRIEVAL_DAEMON_AUTO_START", "1").lower() in ("0", "false", "no"):
        logger.info(
            "Retrieval daemon not running (RETRIEVAL_DAEMON_AUTO_START=0). "
            "Start manually: python scripts/retrieval_daemon.py --port %s",
            RETRIEVAL_DAEMON_PORT,
        )
        return

    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "retrieval_daemon.py"
    if not script.exists():
        logger.warning("Retrieval daemon script not found at %s; skipping auto-start.", script)
        return

    logger.info("Starting retrieval daemon (reranker + embedding warm-up)...")
    # Run in foreground (no --daemon): avoids macOS fork+PyTorch MPS crash (SIGSEGV).
    # Daemon runs as subprocess; stays up for subsequent agent runs.
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--port", str(RETRIEVAL_DAEMON_PORT)],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        proc.wait(timeout=5)
        # Exited within 5s → likely crashed
        if proc.returncode != 0:
            err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            logger.warning("Retrieval daemon exited with code %s: %s", proc.returncode, err[:500])
            return
    except subprocess.TimeoutExpired:
        # Still running (foreground mode) → expected
        pass
    except Exception as e:
        logger.warning("Failed to start retrieval daemon: %s", e)
        return

    for _ in range(_DAEMON_START_TIMEOUT):
        if _retrieval_daemon_health(RETRIEVAL_DAEMON_PORT):
            logger.info("Retrieval daemon started and ready.")
            return
        time.sleep(_DAEMON_POLL_INTERVAL)

    logger.warning(
        "Retrieval daemon did not become ready within %ss. "
        "Agent will use in-process models (may have cold-start).",
        _DAEMON_START_TIMEOUT,
    )


def _check_endpoint_reachable(url: str) -> tuple[bool, str]:
    """Probe endpoint; return (reachable, error_message)."""
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        req = urllib.request.Request(base, method="GET")
        with urllib.request.urlopen(req, timeout=_STARTUP_TIMEOUT_SEC) as resp:
            _ = resp.read()
        return True, ""
    except urllib.error.URLError as e:
        if e.reason and "Connection refused" in str(e.reason):
            return False, f"Connection refused: {url}"
        if e.reason and "timed out" in str(e.reason).lower():
            return False, f"Timeout: {url}"
        return False, str(e.reason) if e.reason else str(e)
    except Exception as e:
        return False, str(e)


def _ensure_reranker_ready() -> None:
    """Ensure reranker is initialized before any other work. Log info if starting."""
    from config.retrieval_config import RERANKER_ENABLED, RERANKER_STARTUP

    if not RERANKER_STARTUP:
        logger.info("Reranker startup disabled (RERANKER_STARTUP=0); skipping auto-init.")
        return
    if not RERANKER_ENABLED:
        logger.info("Reranker disabled (RERANKER_ENABLED=0); skipping init.")
        return

    from agent.retrieval.reranker import create_reranker, init_reranker

    if create_reranker() is not None:
        logger.info("Reranker already running.")
        return

    logger.info(
        "Reranker not running; initializing now before any other work."
    )
    init_reranker()
    r = create_reranker()
    if r is None:
        logger.warning(
            "Reranker init failed (model missing or disabled). Pipeline will use LLM ranker fallback (~100× slower). "
            "Fix: pip install onnxruntime, python scripts/download_reranker.py --device cpu"
        )
    else:
        logger.info("Reranker initialized and ready.")


def _verify_llm_endpoints() -> None:
    """Verify all required LLM endpoints are reachable. Exit with clear error if not."""
    from agent.models.model_config import TASK_MODELS, get_endpoint_for_model, get_model_name

    seen: set[str] = set()
    unreachable: list[tuple[str, str, str]] = []

    for _task, model_key in TASK_MODELS.items():
        key = (model_key or "").strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        endpoint = get_endpoint_for_model(key)
        name = get_model_name(key)
        ok, err = _check_endpoint_reachable(endpoint)
        if not ok:
            unreachable.append((key, endpoint, err))

    if unreachable:
        lines = [
            "LLM model endpoints are not reachable. Start the model services first.",
            "",
        ]
        for key, endpoint, err in unreachable:
            lines.append(f"  {key}: {endpoint}")
            lines.append(f"    Error: {err}")
        lines.append("")
        lines.append("Example: run llama.cpp servers on the configured ports.")
        msg = "\n".join(lines)
        logger.error(msg)
        print(msg, file=sys.stderr)
        sys.exit(1)


def ensure_services_ready() -> None:
    """Bootstrap before main: ensure retrieval daemon, init models, reranker, verify LLM endpoints. Exit on LLM failure."""
    if os.getenv("SKIP_STARTUP_CHECKS", "").lower() in ("1", "true", "yes"):
        logger.info("SKIP_STARTUP_CHECKS=1; skipping model init, reranker, and LLM reachability check.")
        return
    _ensure_retrieval_daemon_running()
    try:
        from agent.runtime.agent_boot import boot

        boot()
    except Exception as e:
        logger.debug("Model bootstrap skipped: %s", e)
    _ensure_reranker_ready()
    _verify_llm_endpoints()
