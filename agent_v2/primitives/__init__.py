"""Primitive layer accessors and defaults."""

from agent_v2.primitives.browser import Browser
from agent_v2.primitives.editor import Editor
from agent_v2.primitives.shell import Shell

_default_shell = Shell()
_default_editor = Editor()
_default_browser = Browser()


def get_shell(state=None) -> Shell:
    if state is not None and getattr(state, "context", None):
        shell = state.context.get("shell")
        if shell is not None:
            return shell
    return _default_shell


def get_editor(state=None) -> Editor:
    if state is not None and getattr(state, "context", None):
        editor = state.context.get("editor")
        if editor is not None:
            return editor
    return _default_editor


def get_browser(state=None) -> Browser:
    if state is not None and getattr(state, "context", None):
        browser = state.context.get("browser")
        if browser is not None:
            return browser
    return _default_browser

