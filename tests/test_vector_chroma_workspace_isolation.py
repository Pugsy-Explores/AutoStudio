"""Chroma client must be per workspace; a process-global singleton breaks multi-workspace eval (agent_eval A/B)."""

from __future__ import annotations

import pytest

from agent.retrieval.vector_retriever import _get_client, reset_chroma_clients_for_tests


def test_chroma_client_distinct_per_project_root(tmp_path):
    reset_chroma_clients_for_tests()
    a = tmp_path / "ws_a"
    b = tmp_path / "ws_b"
    a.mkdir()
    b.mkdir()
    try:
        ca = _get_client(str(a))
        cb = _get_client(str(b))
    except Exception as e:
        pytest.skip(f"chromadb unavailable or init failed: {e}")
    assert ca is not None and cb is not None
    assert ca is not cb, "singleton Chroma client would mix embeddings paths across workspaces"
    assert _get_client(str(a)) is ca


def test_reset_chroma_clients_clears_cache(tmp_path):
    reset_chroma_clients_for_tests()
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        c1 = _get_client(str(ws))
    except Exception as e:
        pytest.skip(f"chromadb unavailable: {e}")
    reset_chroma_clients_for_tests()
    c2 = _get_client(str(ws))
    assert c1 is not c2
