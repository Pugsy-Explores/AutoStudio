"""
Phase 5.3 — explicit semantic facts: append-only JSONL, token-overlap query only.

No embeddings, scoring, or deduplication.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Cap how many trailing JSONL rows are scanned per query (bounded I/O).
MAX_FACTS_READ = 1000


def _utc_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_last_lines(path: Path, max_lines: int) -> list[str]:
    """
    Read at most the last ``max_lines`` newline-separated rows without loading
    the whole file when it is large.

    If the read starts mid-file, the first decoded line may be a fragment; that
    fragment is dropped.
    """
    if max_lines <= 0:
        return []
    if not path.is_file():
        return []
    size = path.stat().st_size
    if size == 0:
        return []
    buf = bytearray()
    block = min(65536, size)
    pos = size
    with path.open("rb") as f:
        while pos > 0:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            buf[:0] = f.read(step)
            if buf.count(b"\n") >= max_lines:
                break
    text = buf.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    if pos > 0 and all_lines:
        all_lines = all_lines[1:]
    if len(all_lines) > max_lines:
        all_lines = all_lines[-max_lines:]
    return all_lines


def _fact_word_set(obj: dict[str, Any]) -> set[str]:
    tl = obj.get("text_lower")
    if isinstance(tl, str) and tl.strip():
        raw = tl
    else:
        raw = str(obj.get("text", "")).lower()
    return set(raw.split())


class SemanticMemory:
    """Append-only store of explicit facts; word-level token overlap on ``text``."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._path = self._base_dir / "facts.jsonl"

    def add_fact(
        self,
        key: str,
        text: str,
        *,
        tags: Optional[list[str]] = None,
        source: Optional[str] = None,
    ) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        text_lower = text.lower()
        record: dict[str, Any] = {
            "key": key,
            "text": text,
            "text_lower": text_lower,
            "tags": list(tags) if tags is not None else [],
            "timestamp": _utc_timestamp_iso(),
        }
        if source is not None:
            record["source"] = source
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)

    def query(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        query_tokens = [t for t in query.lower().split() if t]
        if not query_tokens or limit <= 0:
            return []
        if not self._path.is_file():
            return []

        raw_lines = _read_last_lines(self._path, MAX_FACTS_READ)
        matches: list[tuple[float, int, dict[str, Any]]] = []
        for line_no, raw in enumerate(raw_lines):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            fact_words = _fact_word_set(obj)
            if not any(token in fact_words for token in query_tokens):
                continue
            ts = obj.get("timestamp", "")
            sort_ts = _parse_timestamp_for_sort(ts)
            matches.append((sort_ts, line_no, obj))

        matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [m[2] for m in matches[:limit]]


def _parse_timestamp_for_sort(ts: Any) -> float:
    if ts is None:
        return 0.0
    s = str(ts).strip()
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OSError):
        return 0.0
