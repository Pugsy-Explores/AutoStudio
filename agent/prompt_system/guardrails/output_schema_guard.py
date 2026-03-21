"""Validate LLM response against PromptTemplate.output_schema."""

import json
import logging
from typing import Any

from agent.utils.json_utils import safe_json_loads

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict | list | None:
    """Extract first valid JSON object or array from text. Uses safe_json_loads for parsing."""
    data, err, repaired = safe_json_loads(text)
    if data is None:
        return None
    if repaired:
        logger.warning("[json] repaired malformed JSON response")
    return data if isinstance(data, (dict, list)) else None


def _validate_against_schema(obj: Any, schema: dict) -> tuple[bool, str]:
    """
    Validate object against a simple schema.
    Schema format: {"type": "object", "properties": {...}, "required": [...]}
    Returns (is_valid, error_message).
    """
    if schema is None:
        return True, ""

    schema_type = schema.get("type", "object")
    if schema_type == "object":
        if not isinstance(obj, dict):
            return False, f"Expected object, got {type(obj).__name__}"
        required = schema.get("required", [])
        for key in required:
            if key not in obj:
                return False, f"Missing required key: {key}"
        properties = schema.get("properties", {})
        for key, prop_schema in properties.items():
            if key in obj and isinstance(prop_schema, dict):
                valid, msg = _validate_against_schema(obj[key], prop_schema)
                if not valid:
                    return False, f"{key}: {msg}"
    elif schema_type == "array":
        if not isinstance(obj, list):
            return False, f"Expected array, got {type(obj).__name__}"
    return True, ""


def validate_output_schema(
    response: str,
    output_schema: dict | None,
) -> tuple[bool, str]:
    """
    Validate LLM response against output_schema.
    Returns (is_valid, error_message).
    If output_schema is None, only checks that response contains valid JSON.
    """
    if output_schema is None:
        obj = _extract_json(response)
        if obj is None and response.strip():
            return False, "Response does not contain valid JSON"
        return True, ""

    obj = _extract_json(response)
    if obj is None:
        return False, "Response does not contain valid JSON"
    return _validate_against_schema(obj, output_schema)
