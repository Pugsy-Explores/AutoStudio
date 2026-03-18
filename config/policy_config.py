"""Policy configuration loader.

This module provides a thin runtime layer over the YAML/JSON configuration in
`config/policy_config.yaml`. It is intentionally small and importable from
policy-related components (execution policy engine, safety guardrails, etc.).

Rule alignment:
- Config lives in data files (`config/policy_config.yaml`), not in code.
- Execution engine and guardrails read policy from this single source of truth.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is optional, JSON fallback is used if missing
    yaml = None  # type: ignore


_DEFAULT_POLICY_CONFIG_PATH = Path(__file__).with_name("policy_config.yaml")


def _load_raw_policy_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load raw policy config from YAML or JSON.

    Precedence:
    1. Explicit path if provided.
    2. `POLICY_CONFIG_PATH` env var.
    3. Default `config/policy_config.yaml` next to this module.
    """
    cfg_path = Path(
        path
        or os.getenv("POLICY_CONFIG_PATH")
        or _DEFAULT_POLICY_CONFIG_PATH
    )
    if not cfg_path.exists():
        raise FileNotFoundError(f"Policy config file not found at {cfg_path}")

    text = cfg_path.read_text(encoding="utf-8")

    # Prefer YAML when available; fall back to JSON.
    if cfg_path.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
        return yaml.safe_load(text) or {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse policy config file {cfg_path}: {e}") from e


def _get_section(raw: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected mapping for policy config section {key!r}, got {type(value).__name__}")
    return dict(value)


@dataclass(frozen=True)
class ExecutionPolicyTable:
    """Structured view over the execution policy configuration."""

    policies: Dict[str, Dict[str, Any]]
    failure_recovery_dispatch: Dict[str, str]
    search_memory_snippet_max: int

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "ExecutionPolicyTable":
        section = _get_section(raw, "policy_engine")
        # Prefer an explicit "policies" sub-mapping if present; otherwise treat the
        # top-level keys (excluding metadata keys) as per-action policies.
        raw_policies: Mapping[str, Any] = section.get("policies") or section
        policies: Dict[str, Dict[str, Any]] = {}
        for action, cfg in raw_policies.items():
            # Skip known non-policy keys when using the section directly.
            if action in {"failure_recovery_dispatch", "search_memory_snippet_max"}:
                continue
            if not isinstance(cfg, Mapping):
                continue
            policies[str(action).upper()] = dict(cfg)

        failure_dispatch = section.get("failure_recovery_dispatch") or {}
        if not isinstance(failure_dispatch, Mapping):
            raise ValueError("policy_engine.failure_recovery_dispatch must be a mapping")
        search_snippet_max = int(section.get("search_memory_snippet_max", 500))
        return cls(
            policies=policies,
            failure_recovery_dispatch={str(k): str(v) for k, v in failure_dispatch.items()},
            search_memory_snippet_max=search_snippet_max,
        )


@dataclass(frozen=True)
class SafetyPolicyDefaults:
    """Default values for `SafetyPolicy` when no explicit instance is provided."""

    allowed_tools: tuple[str, ...]
    forbidden_operations: tuple[str, ...]
    forbidden_patterns: tuple[str, ...]

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "SafetyPolicyDefaults":
        section = _get_section(raw, "safety_policy")
        allowed = tuple(str(t) for t in section.get("allowed_tools") or [])
        forbidden_ops = tuple(str(o) for o in section.get("forbidden_operations") or [])
        forbidden_patterns = tuple(str(p) for p in section.get("forbidden_patterns") or [])
        return cls(
            allowed_tools=allowed,
            forbidden_operations=forbidden_ops,
            forbidden_patterns=forbidden_patterns,
        )


def load_execution_policy_table(path: str | os.PathLike | None = None) -> ExecutionPolicyTable:
    """Load and return the execution policy table."""
    raw = _load_raw_policy_config(path)
    return ExecutionPolicyTable.from_raw(raw)


def load_safety_policy_defaults(path: str | os.PathLike | None = None) -> SafetyPolicyDefaults:
    """Load default safety policy configuration."""
    raw = _load_raw_policy_config(path)
    return SafetyPolicyDefaults.from_raw(raw)

