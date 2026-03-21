"""Evaluation adapter — decouple system output from evaluation format."""

from typing import Any, Dict


def to_evaluation_view(result: dict) -> dict:
    """
    Normalize system output into evaluation format.

    Must return:
    {
        "structure": {...},
        "metrics": {...},
        "signals": {...}   # optional, raw observability
    }
    """
    return {
        "structure": result.get("structure", {}),
        "metrics": result.get("metrics", {}),
        "signals": result,  # full raw output for debugging
    }
