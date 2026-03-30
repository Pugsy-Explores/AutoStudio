from __future__ import annotations

import json
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
        for candidate in JSONExtractor._iter_json_object_strings(raw):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

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
