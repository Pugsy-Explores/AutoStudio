"""Observability and trace configuration."""

import os
from pathlib import Path


AGENT_MEMORY_DIR = os.getenv("AGENT_MEMORY_DIR", ".agent_memory")
TRACES_SUBDIR = os.getenv("TRACES_SUBDIR", "traces")
MAX_TRACE_SIZE_BYTES = int(os.getenv("MAX_TRACE_SIZE_BYTES", str(500 * 1024)))

_DOTENV_LOADED = False


def load_repo_dotenv_if_present() -> None:
    """Load repo .env once so observability env keys are available."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _DOTENV_LOADED = True
        return
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    _DOTENV_LOADED = True


def get_langfuse_host() -> str:
    """
    Langfuse Python SDK uses `host` (LANGFUSE_HOST).
    Many deployments set LANGFUSE_BASE_URL in .env — support both.
    """
    h = (os.environ.get("LANGFUSE_HOST") or "").strip()
    if h:
        return h.rstrip("/")
    base = (os.environ.get("LANGFUSE_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    return "https://cloud.langfuse.com"


def has_langfuse_keys() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))


def get_langfuse_keys() -> tuple[str | None, str | None]:
    return os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")


def get_langfuse_root_name_env() -> str:
    return (os.environ.get("AGENT_V2_LANGFUSE_ROOT_NAME") or "").strip()


def get_pytest_nodeid_env() -> str:
    return (os.environ.get("AGENT_V2_PYTEST_NODEID") or "").strip()


def get_trace_dir(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/traces/ under project root."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / TRACES_SUBDIR
