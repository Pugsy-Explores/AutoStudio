"""Golden test schema — constraint-based, no exact output matching."""

from typing import Any, Dict, List, Optional, TypedDict


class GoldenTest(TypedDict):
    id: str
    input: Dict[str, Any]
    expected: Dict[str, Any]
    llm_judge: Optional[Dict[str, Any]]
