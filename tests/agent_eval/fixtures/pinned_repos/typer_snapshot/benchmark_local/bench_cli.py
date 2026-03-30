"""Minimal Typer app for feature benchmark."""

from __future__ import annotations

import typer

app = typer.Typer()


@app.command()
def hello() -> None:
    typer.echo("hello")


def describe_app() -> str:
    """Return a one-line description (benchmark: implement returning a non-empty string)."""
    return ""
