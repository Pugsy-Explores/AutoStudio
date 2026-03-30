"""Engine entrypoint."""

from __future__ import annotations

from flowkit import dispatch
from flowkit.settings import Settings, load


def run(event: str) -> str:
    settings: Settings = load()
    if settings.timeout <= 0:
        return "bad"
    return dispatch.handle(event)
