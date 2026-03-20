"""Pytest configuration for integration tests.

Integration tests use real services (LLM, retrieval, reranker).
No mocks. Run with: pytest tests/integration/ -v

Requires:
- TEST_MODE=integration
- ENABLE_REAL_LLM=true (or unset, integration implies real LLM)
- Reranker service running
- Reasoning model API reachable
"""

import os

# Pre-import numpy before retrieval/reranker to avoid RecursionError in Python 3.12
import numpy  # noqa: F401

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks test as integration test (requires real services)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless TEST_MODE=integration."""
    if os.environ.get("TEST_MODE", "").lower() != "integration":
        skip = pytest.mark.skip(
            reason="Integration tests require TEST_MODE=integration. "
            "Run: TEST_MODE=integration pytest tests/integration/ -v"
        )
        for item in items:
            if "integration" in item.nodeid:
                item.add_marker(skip)
