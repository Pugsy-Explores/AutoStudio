"""Model type enum for routing."""

from enum import Enum


class ModelType(str, Enum):
    """Which model should handle a task."""

    SMALL = "SMALL"
    REASONING = "REASONING"
