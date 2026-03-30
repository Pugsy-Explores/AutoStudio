# Phase 12.6.F — Exploration Scoper (LLM breadth reduction before selection)

**Scope:** Additive **internal** control-plane layer on top of Phase 12.5 / 12.6. **Does not** change **`ExplorationResult` (Schema 4)**, **`UnderstandingAnalyzer`**, or **`CandidateSelector`** semantics — only **what candidate list** reaches `select_batch`. **Not** a new public contract: **do not** amend **`SCHEMAS.md`** for this layer.

**Relationship:** Sits **after** discovery merge/dedup and **cap K**, **before** `_enqueue_ranked` → `CandidateSelector.select_batch`. At most **one** extra LLM call per enqueue when the capped list has **more than five** candidates (see §6).

---

## 1. Problem statement (why 12.6.F exists)

### 1.1 Primary value: fix implicit ordering bias (not only “fewer analyzer calls”)

Today, **`CandidateSelector.select_batch`** only considers **`candidates[:10]`** before its LLM call.

So:

```text
retrieval / discovery → e.g. 30 merged candidates
  → selector LLM sees only the first 10
  → candidates 11–30 are never visible to any LLM in the selection path
```

That is **implicit bias by merge/dedup order**, not an explicit policy. Breadth is high **before** reasoning focuses, and the selector cannot correct for hits that never appear in its window.

**Exploration Scoper** takes a **deterministically capped** slice (see §3) so the **LLM can see up to K items** (e.g. 20), choose a **focused subset by index**, and hand **clean input** to the selector. The selector then does what it already does: **rank / batch** for the queue under `limit` and `seen_files`.

Secondary benefit: fewer low-value targets entering inspect → fewer wasted analyzer calls. The **architectural** win is **visibility + subset**, not only call count.

### 1.2 Expected impact (qualitative)

| Before | After |
|--------|--------|
| Selector sees a **noisy first 10**; order-biased | Scoper can consider **up to K**; emits a **dense** subset |
| Wrong candidates enter loop → analyzer fires often | Scoper narrows **20 → ~5–8** typical → fewer inspections / analyzer calls |

### 1.3 Responsibility boundary (locked)

| Layer | Owns |
|--------|------|
| **Scoper** | **Subset** of indices in the capped list (no ordering semantics). |
| **Selector** | **Ordering** / next-batch ranking, `no_relevant_candidate`, **`seen_files`** — **unchanged**. |
| **Analyzer** | **Reasoning** after inspection — **unchanged**. |

Scoper **does not** rank for visit order; selector **does not** solve “candidates 11–30 never seen.”

---

## 2. Locked control flow

**Final shape (lock this):**

```text
retrieval (SEARCH)
  → merge / dedup
  → cap to K (e.g. 20) — prompt budget

  → if len(capped) <= skip_below:
        scoped = capped   (engine — no scoper LLM)
     else:
        scoped = scoper.scope(...)   (LLM, 1 call)

  → select_batch (LLM, unchanged) — applies final batch limit
  → inspect
  → analyzer (unchanged gating)
  → …
```

No extra loops. **Integration point:** `ExplorationEngineV2._enqueue_ranked` — applies cap `K`, **skip-below orchestration**, optional `scope()`, then `select_batch` (single place for final truncation via `limit`).

---

## 3. Input cap and truncation (not heuristics)

| Mechanism | Role |
|-----------|------|
| **`K` (e.g. 20)** | **Prompt / budget cap** — deterministic slice of the post-dedup list (e.g. first `K` in stable order). **Not** relevance scoring. |
| **`MAX_SNIPPET_CHARS`** (existing engine cap, e.g. `ExplorationEngineV2.MAX_SNIPPET_CHARS`) | Truncate `snippet` in the **wire payload to the scoper LLM** only. |

If discovery produces **≤ K** candidates, the capped list is the full list.

---

## 4. Interface (internal only)

### 4.1 Python surface

```text
scope(instruction: str, candidates: list[ExplorationCandidate]) -> list[ExplorationCandidate]
```

Precondition: `candidates` is already the **capped** list (length ≤ `K`). **Skip when trivial** (`len(capped) <= skip_below`) is implemented in **`ExplorationEngineV2`** — `ExplorationScoper.scope` is **not** invoked in that case (pure transform when called).

- **Empty list** → `scope` returns `[]` (engine should not call `scope` with empty capped list for the enqueue path).

### 4.2 LLM wire format

**Payload to model** — ordered array with stable indices:

```json
[
  { "index": 0, "file_path": "...", "snippet": "..." },
  { "index": 1, "file_path": "...", "snippet": "..." }
]
```

Use **`file_path`** (align with existing types); snippets truncated per §3. Optionally include `source` if useful for the prompt; **indices** are the only join key back to runtime objects.

**Strict JSON output:**

```json
{ "selected_indices": [2, 5, 9] }
```

**Indices only** — no `file_path` / `symbol` matching in Python for this layer.

---

## 5. Deterministic behavior (tightened)

### 5.1 Parse and validate

```text
Parse JSON → extract selected_indices
  → keep only integers in [0, len(candidates)-1]
  → dedupe → valid_set
  → if parse failed OR invalid shape OR valid_set is empty:
        return candidates unchanged (pass-through)
```

**Out-of-range indices:** drop. **Duplicates in LLM output:** dedupe via set.

### 5.2 Order preservation (mandatory)

Scoper is **subset only**, not an ordering layer. The LLM may return `[9, 2, 5]`; that must **not** change traversal order relative to the original capped list.

**After validation:**

```text
sorted_indices = sorted(valid_set)
return [candidates[i] for i in sorted_indices]
```

**No second output cap in code** — `CandidateSelector.select_batch` and its `limit` provide the only batch truncation after scoping. A second cap (`max_indices`) would duplicate that and risk **implicit relevance** cuts.

**Never** iterate indices in LLM-return order.

### 5.3 Pass-through rule (unchanged)

- **Invalid JSON / wrong shape** → pass-through.
- **Empty `valid_set` after filtering** → pass-through.
- **Non-empty valid selection** → apply §5.2 and return (trust the subset; **no** “if len < m then fallback”).

---

## 6. Execution policy — skip trivial lists (engine)

| Condition | Behavior (in `ExplorationEngineV2._enqueue_ranked`) |
|-----------|----------|
| `len(capped) == 0` | Nothing to enqueue. |
| `len(capped) <= skip_below` | **Do not call** `scoper.scope`; pass `capped` straight to `select_batch`. **Not** relevance logic — **execution efficiency** when the capped list is already small. |
| `len(capped) > skip_below` | `scoped = scoper.scope(instruction, capped)` then `select_batch(..., scoped, ...)`. |

**ExplorationScoper** stays a **pure** index subset transform; orchestration stays in the engine.

---

## 7. Contract and observability

- **No** new entries in **`SCHEMAS.md`** for scoper I/O.
- **Recommended (small):** trace or debug fields for tuning — does not change **`ExplorationResult`**:
  - `scoper_input_n` — length of capped list passed to scoper (when scoper runs)
  - `scoper_output_n` — length of list after scoper
  - `scoper_selected_ratio` — `scoper_output_n / scoper_input_n` when scoper ran (detect over- vs under-selection)
  - optional: `scoper_skipped` — true when engine skipped scoper (`len(capped) <= skip_below`)

---

## 8. LLM prompt (final — minimal)

Use a **single** system/user template; **no** chain-of-thought, **no** scores, **no** “top K relevant” in code — guidance only in prose.

**Prompt (lock intent, wording may vary slightly in implementation):**

```text
You are selecting which code locations are worth exploring for a task.

You are given:
- an instruction
- a list of candidate code snippets from a repository

Your job:
Return a subset of candidate indices that are likely relevant to solving the instruction.

Guidelines:
- Prefer implementation logic over tests, mocks, or configs
- Prefer files that appear to contain core logic related to the task
- Ignore clearly unrelated files
- Keep the selection focused (do not select everything unless necessary)
- Selecting all candidates is usually a mistake unless all are clearly relevant
- Do NOT rank or order — only select indices

IMPORTANT:
- Only choose from the given indices
- Do not invent new files or indices
- If none are relevant, return an empty list

---

Instruction:
{instruction}

Candidates:
{candidates_json}

---

Return JSON ONLY in this format:

{
  "selected_indices": [ ... ]
}
```

**Prompt hygiene — do not add:**

```text
❌ “top K” / scoring language in the prompt that implies ranked retrieval in code
❌ Reasoning output, explanations, chain-of-thought
❌ Extra structured fields beyond selected_indices
```

---

## 9. Implementation checklist (concrete)

| Item | Notes |
|------|--------|
| **`agent_v2/exploration/exploration_scoper.py`** | `ExplorationScoper.scope(...)`; JSON parse; validate; **sort**; pass-through; **no** output index cap; `scoper_selected_ratio` in debug log on success. |
| **`exploration_engine_v2.py`** | After dedup: cap `K` → if `len(capped) > skip_below` then `scope` else `capped` → `select_batch`. |
| **`exploration_runner.py` / bootstrap** | Inject `llm_generate`. |
| **`agent_v2/config.py`** | `EXPLORATION_SCOPER_K` (default 20), `EXPLORATION_SCOPER_SKIP_BELOW` (default 5). |
| **Tests** | Engine: skip scoper when capped len ≤ skip_below; scoper: invalid JSON / empty selection → pass-through; sorted index order; snippet truncation. |

**Do not modify:** `candidate_selector.py` prompts or `UnderstandingAnalyzer` in this phase.

---

## 10. Non-goals

```text
❌ Python relevance scoring, lexical filters, or score thresholds on candidates
❌ Changing selector or analyzer behavior
❌ Path/symbol-based rehydration from LLM output
❌ Under-selection thresholds (“if fewer than m, fallback”)
❌ Formalizing scoper JSON in SCHEMAS.md
❌ Using LLM-return order for rehydration (must use sorted indices)
```

---

## 11. Alignment with architecture freeze

| Rule | Phase 12.6.F |
|------|----------------|
| Extend, do not replace engine | Adds one module + one call site |
| Selector remains sole “next batch” ranking boundary | Scoper only reduces **input breadth** by index; **sorted** rehydration preserves scoper-as-subset |
| No second public planner contract | Internal wire format only |

---

## 12. Principal-engineer verdict

- **Correct layer:** fix breadth **before** deep reasoning (inspect/analyze), not inside the analyzer.
- **Correct abstraction:** subset (scoper) vs ordering (selector) vs reasoning (analyzer).
- **Correct safety:** pass-through on failure/empty selection; **no** silent drop of retrieval.
- **Correct tightenings:** trivial skip in **engine** (`len(capped) ≤ skip_below`), **sorted** rehydration, **no** second output cap (selector owns final `limit`).

---

**Authoritative doc index:** **`PHASE_12_6_EXPLORATION_CONTROL_SEMANTICS.md`** (12.6), **`PHASE_12_5_EXPLORATION_ENGINE_V2.md`** (12.5). This document is **12.6.F** — exploration **scoping** before selection.
