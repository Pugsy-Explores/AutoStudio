"""
Phase 12 — Execution graph API server (optional).

FastAPI backend for serving execution graphs to the UI.

Production requirements:
- Authentication (API key or OAuth)
- CORS configuration
- Rate limiting
- No arbitrary code execution via query params

v1: Health check + basic graph endpoint.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent_v2.observability.graph_builder import build_graph
from agent_v2.schemas.trace import Trace

_LOG = logging.getLogger(__name__)

app = FastAPI(title="AutoStudio Execution Graph API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GraphRequest(BaseModel):
    """Request body for graph generation."""

    trace: dict


class GraphResponse(BaseModel):
    """Response containing execution graph."""

    graph: dict
    trace_id: str


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "execution-graph-api"}


@app.post("/graph", response_model=GraphResponse)
def create_graph(request: GraphRequest) -> GraphResponse:
    """
    Generate execution graph from trace.

    POST /graph
    Body: {"trace": {...}}
    Returns: {"graph": {...}, "trace_id": "..."}
    """
    try:
        trace = Trace(**request.trace)
        graph = build_graph(trace)
        return GraphResponse(
            graph=graph.model_dump(),
            trace_id=trace.trace_id,
        )
    except Exception as e:
        _LOG.exception("Failed to build graph")
        raise HTTPException(status_code=400, detail=f"Failed to build graph: {e}")


@app.get("/")
def root() -> dict[str, str]:
    """Root endpoint with API info."""
    return {
        "service": "AutoStudio Execution Graph API",
        "version": "0.1.0",
        "endpoints": {
            "health": "GET /health",
            "graph": "POST /graph",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
