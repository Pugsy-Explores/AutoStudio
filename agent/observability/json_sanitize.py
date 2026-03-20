"""Depth- and cycle-bounded JSON-compatible trees for telemetry and harness snapshots."""

from __future__ import annotations

from typing import Any


def json_safe_tree(obj: Any, *, max_depth: int = 64, max_list_len: int = 512, max_str_len: int = 200_000) -> Any:
    """
    Convert ``obj`` to a structure json.dumps can encode without recursion issues.
    - Caps nesting depth (prevents pathological deep dict/list trees).
    - Breaks cycles via id() tracking for dict/list.
    - Truncates long strings and large containers.
    """
    seen: set[int] = set()

    def _walk(x: Any, depth: int) -> Any:
        if depth > max_depth:
            return "<max_depth>"
        if isinstance(x, str):
            return x if len(x) <= max_str_len else x[:max_str_len] + "…"
        if isinstance(x, (int, float, bool)) or x is None:
            return x
        if isinstance(x, (bytes, bytearray)):
            return bytes(x[: max_str_len]).decode("utf-8", errors="replace")
        if isinstance(x, dict):
            i = id(x)
            if i in seen:
                return "<cycle>"
            seen.add(i)
            try:
                out: dict[Any, Any] = {}
                for k, v in list(x.items())[:max_list_len]:
                    key = k if isinstance(k, str) else str(k)
                    if len(key) > 400:
                        key = key[:400] + "…"
                    out[key] = _walk(v, depth + 1)
                return out
            finally:
                seen.discard(i)
        if isinstance(x, (list, tuple)):
            i = id(x)
            if i in seen:
                return "<cycle>"
            seen.add(i)
            try:
                return [_walk(v, depth + 1) for v in list(x)[:max_list_len]]
            finally:
                seen.discard(i)
        try:
            s = str(x)
        except Exception:
            return "<unserializable>"
        return s if len(s) <= max_str_len else s[:max_str_len] + "…"

    return _walk(obj, 0)
