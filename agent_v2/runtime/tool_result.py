"""Normalized runtime tool result contract."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    success: bool
    output: Any = None
    error: str | None = None

    @classmethod
    def from_any(cls, result: Any) -> "ToolResult":
        if isinstance(result, cls):
            return result

        if isinstance(result, dict):
            return cls(
                success=bool(result.get("success", True)),
                output=result.get("output"),
                error=result.get("error"),
            )

        return cls(
            success=bool(getattr(result, "success", True)),
            output=getattr(result, "output", None),
            error=getattr(result, "error", None),
        )
