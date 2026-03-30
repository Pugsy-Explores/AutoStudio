"""
Tool schemas — ToolCall, ToolError, ToolResult.

ToolResult is the raw output from a tool handler BEFORE normalization into ExecutionResult.
Constraint: ToolResult MUST NOT include step_id (step binding happens in ExecutionResult).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict


class ToolError(BaseModel):
    type: str
    message: str
    details: dict = {}


class ToolResult(BaseModel):
    """
    Raw tool output. step_id is intentionally absent — step binding lives in ExecutionResult.
    Normalization from ToolResult → ExecutionResult happens in the dispatcher layer.
    """
    tool_name: str
    success: bool
    data: dict
    error: Optional[ToolError] = None
    duration_ms: int
    raw: Optional[dict] = None
