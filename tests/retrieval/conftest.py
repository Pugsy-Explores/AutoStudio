"""Retrieval test defaults: use local Chroma / in-process paths unless a daemon is running."""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _retrieval_tests_prefer_local_vector_store():
    """Must run before module-scoped fixtures (e.g. retrieval_engine) so remote-first is off at import/warmup."""
    os.environ["RETRIEVAL_REMOTE_FIRST"] = "0"
    yield
