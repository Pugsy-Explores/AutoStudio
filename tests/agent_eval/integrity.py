"""
Stage 32 — Integrity validation and field enrichment.

Responsibilities:
- ensure_integrity_fields (mocked mode defaults)
- REQUIRED_INTEGRITY_FIELDS for audit/summary
"""

from __future__ import annotations

from typing import Any

REQUIRED_INTEGRITY_FIELDS: tuple[str, ...] = (
    "execution_mode",
    "model_call_count",
    "small_model_call_count",
    "reasoning_model_call_count",
    "used_offline_stubs",
    "used_plan_injection",
    "used_explain_stub",
    "integrity_valid",
    "integrity_failure_reason",
)


def ensure_integrity_fields(structural: dict[str, Any], *, execution_mode: str) -> dict[str, Any]:
    """Ensure canonical integrity fields exist. Mocked mode has no model calls."""
    if execution_mode != "mocked":
        return structural
    structural["model_usage_audit"] = structural.get("model_usage_audit") or {
        "model_call_count": 0,
        "small_model_call_count": 0,
        "reasoning_model_call_count": 0,
    }
    structural.setdefault("used_offline_stubs", False)
    structural.setdefault("used_plan_injection", False)
    structural.setdefault("used_explain_stub", False)
    structural.setdefault("live_model_integrity_ok", False)
    structural.setdefault("integrity_failure_reason", "mocked_mode")
    structural.setdefault("stub_audit", {"used_offline_stubs": False, "patched_call_sites": [], "used_explain_stub": False})
    return structural


def build_extra_integrity(structural: dict[str, Any], execution_mode: str) -> dict[str, Any]:
    """Build integrity-related extra dict for TaskOutcome."""
    return {
        "model_usage_audit": structural.get("model_usage_audit"),
        "plan_resolution_telemetry": structural.get("plan_resolution_telemetry"),
        "stub_audit": structural.get("stub_audit"),
        "used_offline_stubs": structural.get("used_offline_stubs"),
        "used_plan_injection": structural.get("used_plan_injection"),
        "used_explain_stub": structural.get("used_explain_stub"),
        "live_model_integrity_ok": structural.get("live_model_integrity_ok"),
        "integrity_valid": structural.get("live_model_integrity_ok", False),
        "integrity_failure_reason": structural.get("integrity_failure_reason"),
    }
