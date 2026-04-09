from __future__ import annotations

import json
import re
from typing import Callable


class JSONExtractor:
    @staticmethod
    def extract_final_json(text: str, validate_fn: Callable[[dict], bool] | None = None) -> dict:
        candidates = JSONExtractor.extract_all_json_candidates(text)
        if validate_fn is None:
            if candidates:
                return candidates[-1]
        else:
            for obj in reversed(candidates):
                if validate_fn(obj):
                    return obj
        raw = text or ""
        tail = raw[-500:]
        raise ValueError(
            "No valid JSON object found (last-valid-json policy). "
            f"Candidates tried: {len(candidates)}, tail: {tail}"
        )

    @staticmethod
    def extract_all_json_candidates(text: str) -> list[dict]:
        raw = text or ""
        out: list[dict] = []
        # Prefer fenced-code payloads first when present (```json ... ```),
        # then fall back to brace scanning over the full text.
        for fenced in JSONExtractor._iter_fenced_blocks(raw):
            try:
                parsed = json.loads(fenced)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        for candidate in JSONExtractor._iter_json_object_strings(raw):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        # Last resort: support compact YAML-like key/value outputs sometimes emitted
        # by local reasoning models, e.g. `selected_indices: [0]`.
        kv = JSONExtractor._parse_top_level_key_value_object(raw)
        if isinstance(kv, dict) and kv:
            out.append(kv)
        return out

    @staticmethod
    def _iter_fenced_blocks(text: str):
        """
        Yield code-fence payloads with/without language tags.
        Supports ```json ... ```, ```JSON ... ```, and bare ``` ... ```.
        """
        if not text:
            return
        for m in re.finditer(r"```(?:\s*json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE):
            payload = (m.group(1) or "").strip()
            if payload:
                yield payload

    @staticmethod
    def _iter_json_object_strings(text: str):
        in_string = False
        escape = False
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
                continue
            if ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : i + 1]
                    start = -1

    @staticmethod
    def _parse_top_level_key_value_object(text: str) -> dict:
        """
        Parse a minimal top-level `key: value` object from text when strict JSON object
        parsing fails. Values are parsed via json.loads when possible.
        """
        lines = (text or "").splitlines()
        obj: dict = {}
        for line in lines:
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", line)
            if not m:
                continue
            key = m.group(1)
            raw_val = m.group(2).strip()
            if not raw_val:
                continue
            try:
                parsed = json.loads(raw_val)
            except Exception:
                low = raw_val.lower()
                if low == "true":
                    parsed = True
                elif low == "false":
                    parsed = False
                elif low == "null":
                    parsed = None
                else:
                    parsed = raw_val
            obj[key] = parsed
        return obj
