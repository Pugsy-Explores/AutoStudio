"""Pytest configuration."""

import logging

import pytest

logger = logging.getLogger(__name__)


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
