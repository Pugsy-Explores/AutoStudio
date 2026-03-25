"""Pytest configuration."""

# Pre-import numpy before mocks/threads to avoid RecursionError in Python 3.12
# (numpy + nested import loader; see Docs/RCA_AUDIT12_RECURSION_AND_STUBS.md)
import numpy  # noqa: F401

import importlib
import logging
import os

import pytest

logger = logging.getLogger(__name__)

# Modules required for the default test suite. Install: pip install -e ".[test]" or bash scripts/install_test_deps.sh
_REQUIRED_IMPORTS = ("tree_sitter_python", "rank_bm25")


def pytest_sessionstart(session):
    if os.environ.get("AUTOSTUDIO_SKIP_IMPORT_CHECK") == "1":
        return
    failed: list[str] = []
    for name in _REQUIRED_IMPORTS:
        try:
            importlib.import_module(name)
        except ImportError as e:
            failed.append(f"{name} (ImportError: {e})")
        except RecursionError as e:
            failed.append(
                f"{name} (RecursionError: installed but unusable). "
                "Try: pip install numpy --upgrade && pip install rank-bm25 --force-reinstall"
            )
    if failed:
        pytest.exit(
            "Required packages for tests failed to import:\n  - "
            + "\n  - ".join(failed)
            + "\n\nInstall with: pip install -e \".[test]\" or bash scripts/install_test_deps.sh\n"
            "(Set AUTOSTUDIO_SKIP_IMPORT_CHECK=1 only to bypass in emergencies.)",
            returncode=2,
        )


def pytest_addoption(parser):
    parser.addoption(
        "--llm",
        action="store_true",
        help="Use real LLM for E2E tests (default). If unreachable, warn and fall back to mock.",
    )
    parser.addoption(
        "--mock",
        action="store_true",
        help="Force mock mode; skip LLM probe. Use: pytest tests/test_agent_e2e.py --mock",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")


@pytest.fixture(autouse=True)
def _pytest_langfuse_root_name(request, monkeypatch):
    """
    Unified Langfuse root span naming for any test that calls ``create_agent_trace``.

    Consumed via ``AGENT_V2_LANGFUSE_ROOT_NAME`` (see ``agent_v2.observability.langfuse_client``).

    - ``@pytest.mark.agent_v2_live``: ``live::<pytest node id>``
    - all other tests: ``<pytest node id> - offline``
    """
    nodeid = request.node.nodeid
    monkeypatch.setenv("AGENT_V2_PYTEST_NODEID", nodeid)
    if request.node.get_closest_marker("agent_v2_live") is not None:
        label = f"live::{nodeid}"
    else:
        label = f"{nodeid} - offline"
    monkeypatch.setenv("AGENT_V2_LANGFUSE_ROOT_NAME", label)


@pytest.fixture(scope="session")
def e2e_use_mock(request):
    """
    Determine whether E2E tests use mocked LLM.
    Default (--llm): try real LLM; if unreachable, warn and use mock.
    --mock: always use mock.
    """
    if request.config.getoption("--mock", default=False):
        return True
    # Default: try real LLM
    try:
        from agent.models.model_client import call_small_model

        out = call_small_model("Reply with exactly: ok", task_name="e2e_probe", max_tokens=5)
        if out and (out or "").strip():
            return False
        logger.warning(
            "[e2e] LLM probe returned empty. Using mocked responses. Use --mock to skip probe."
        )
    except Exception as e:
        logger.warning(
            "[e2e] LLM not reachable (%s). Using mocked responses. Use --mock to skip probe.",
            e,
        )
    return True
