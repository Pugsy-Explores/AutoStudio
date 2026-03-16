"""Agent boot: call initialize_models at startup."""

from agent.runtime.model_bootstrap import initialize_models

__all__ = ["initialize_models"]


def boot() -> None:
    """Boot agent: initialize models before any agent work."""
    initialize_models()
