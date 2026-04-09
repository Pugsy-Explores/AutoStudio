# --cursor prompt --
You are a staff engineer implementing Phase 5.3: Semantic Memory (minimal) from @pugsy_ai/.cursor/plans/agentv2_memory_phase_5_6ebe61fb.plan.md 

Follow the Phase 5 plan.
ONLY implement Phase 5.3.

Do NOT modify episodic (5.1) or session (5.2).
Do NOT add embeddings, vector DBs, or learning systems.

---

## GOAL

Add a simple, persistent fact store:

* explicit facts only
* keyword-based retrieval
* no inference, no scoring

---

## STEP 1 — CREATE FACT STORE

Create file:

agent_v2/memory/semantic_memory.py

Implement:

```python
class SemanticMemory:
    def __init__(self, base_dir: Path): ...
    
    def add_fact(
        self,
        key: str,
        text: str,
        *,
        tags: Optional[list[str]] = None,
        source: Optional[str] = None,
    ) -> None: ...
    
    def query(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict]: ...
```

---

## STEP 2 — STORAGE

Use simple JSONL:

Path:
.agent_memory/semantic/facts.jsonl

Each line:

```json
{
  "key": "file:main.py",
  "text": "main.py defines FastAPI app",
  "tags": ["file", "api"],
  "timestamp": "...",
  "source": "optional"
}
```

Append-only.

---

## STEP 3 — QUERY (VERY SIMPLE)

Implement:

* lowercase match
* token overlap (split on whitespace)

Example:

```python
if any(token in fact_text for token in query_tokens):
    include
```

Sort:

* newest first

---

## STEP 4 — NO DEDUP LOGIC (IMPORTANT)

Do NOT:

* merge facts
* overwrite facts
* score facts

Just append + retrieve

---

## STEP 5 — CONFIG

Add in config.py:

```python
def get_semantic_memory_dir() -> Path:
```

Default:
.agent_memory/semantic/

---

## STEP 6 — KEEP ISOLATED

Do NOT:

* inject into planner
* modify prompts
* connect to episodic yet

This is storage layer only

---

## STEP 7 — TESTS

Create:

tests/test_semantic_memory.py

Test:

1. add + query
2. multiple facts
3. keyword filtering
4. recency ordering

---

## OUTPUT

Provide:

1. New module
2. Storage format
3. Example usage

---

## RULES

* no vector DB
* no embeddings
* no ranking systems
* no abstractions

Focus:
👉 explicit facts only, minimal retrieval



# --implementation --

Phase 5.3 is implemented in **AutoStudio** (that’s where `agent_v2` lives; the plan you cited is under `pugsy_ai` but the code paths in the plan match this repo). Episodic, session memory, planner, and `memory/__init__.py` were left unchanged.

### 1. New module

```1:88:/Users/shang/my_work/AutoStudio/agent_v2/memory/semantic_memory.py
"""
Phase 5.3 — explicit semantic facts: append-only JSONL, token-overlap query only.

No embeddings, scoring, or deduplication.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
# ... SemanticMemory.add_fact / .query ...
```

- **`SemanticMemory(base_dir)`** — writes `base_dir / "facts.jsonl"`.
- **`add_fact`** — one JSON object per line; optional `tags`, `source`; always sets UTC `timestamp`.
- **`query`** — lowercases the query, splits on whitespace, keeps facts where **any token is a substring of `text`**; sorts by **newest `timestamp` first**, then higher line number as tie-break; respects `limit`.

### 2. Storage format

- **Path:** `{semantic_dir}/facts.jsonl` (default semantic dir → `.agent_memory/semantic/facts.jsonl`).
- **Line shape:**

```json
{
  "key": "file:main.py",
  "text": "main.py defines FastAPI app",
  "tags": ["file", "api"],
  "timestamp": "2026-04-09T12:00:00.123456+00:00",
  "source": "optional"
}
```

`source` is omitted when not passed. Append-only only (no merge/overwrite/scoring).

### 3. Config

```python
def get_semantic_memory_dir() -> Path:
```

Added in `agent_v2/config.py`: default `Path(".agent_memory/semantic").resolve()`, override with `AGENT_V2_SEMANTIC_MEMORY_DIR` (aligned with other `AGENT_V2_*` dirs).

### 4. Tests

`tests/test_semantic_memory.py` covers add+query, multiple facts + limit, keyword miss/hit, recency (`sleep` between writes), tags/source, config default/override, empty query / `limit=0`, and append-only duplicates. **All 9 tests passed.**

### 5. Example usage

```python
from pathlib import Path

from agent_v2.config import get_semantic_memory_dir
from agent_v2.memory.semantic_memory import SemanticMemory

mem = SemanticMemory(get_semantic_memory_dir())
mem.add_fact(
    "file:main.py",
    "main.py defines the FastAPI app",
    tags=["file", "api"],
    source="bootstrap",
)
rows = mem.query("fastapi", limit=10)
# rows: newest-first list[dict] matching token overlap on `text`
```

-- updates --
Here is what changed.

### 1. Word-level matching (no substring false positives)

Matching uses **whitespace-split words** on the fact side:

```python
fact_words = _fact_word_set(obj)  # set(text_lower.split()) or legacy text.lower().split()
if not any(token in fact_words for token in query_tokens):
    continue
```

So `api` no longer hits `rapid`, and `app` no longer hits `happens`.

### 2. Bounded read: `MAX_FACTS_READ = 1000`

- Module constant **`MAX_FACTS_READ`** (default `1000`).
- **`_read_last_lines`** reads from the **end** of the file in binary chunks until there are at least `MAX_FACTS_READ` newlines or the file is fully read; if the buffer starts mid-line, the **first** decoded line is dropped as a fragment.
- **`query`** only parses those trailing lines.

### 3. `text_lower` on write

- **`add_fact`** stores **`text_lower`** next to **`text`** (single `text.lower()` at insert time).
- **`_fact_word_set`** prefers **`text_lower`** when non-empty; otherwise falls back to **`text`.lower()** for older JSONL rows.

### Tests

- **`test_word_match_not_substring_api_in_rapid`** / **`test_word_match_not_substring_app_in_happens`**
- **`test_query_only_scans_last_n_lines`** (`MAX_FACTS_READ` patched to `3`, five facts, only the last three visible)
- **`test_legacy_jsonl_without_text_lower`**
- **`test_add_and_query`** asserts **`text_lower`** on new facts

All **13** tests pass.