"""Sub module."""

from ..foo import bar

class MyClass:
    """A class."""

    def method_a(self):
        bar()
        return "a"

    def method_b(self):
        self.method_a()
        return "b"
