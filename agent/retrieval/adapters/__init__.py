"""Thin adapters: each source retriever → list[dict] in legacy {file, symbol, line, snippet} format.

Each adapter adds a 'source' key and a 'metadata' dict to every row.
No scoring, no filtering, no heuristics.
"""
from agent.retrieval.adapters.bm25 import fetch_bm25
from agent.retrieval.adapters.graph import fetch_graph
from agent.retrieval.adapters.serena import fetch_serena
from agent.retrieval.adapters.vector import fetch_vector

__all__ = ["fetch_graph", "fetch_bm25", "fetch_vector", "fetch_serena"]
