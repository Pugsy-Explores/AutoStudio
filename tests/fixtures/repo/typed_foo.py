"""Fixture with type hints and docstrings for testing."""

def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b

def greet(name: str = "world") -> str:
    """Return a greeting string."""
    return f"Hello, {name}!"
