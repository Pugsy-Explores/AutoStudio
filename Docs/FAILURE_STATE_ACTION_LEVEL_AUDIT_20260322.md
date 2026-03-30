# Audit — Current Failure State vs Required Action-Level State

**Date:** 2026-03-22

---

## 1. Current failure_state Structure

**Location:** `agent/runtime/execution_loop.py` (init ~L223, update via `_update_failure_state` ~L836)

**Exact structure:**
```python
context["failure_state"] = {
    "failures": [],           # list[str] — failure_explanation strings
    "attempted_patches": [],  # list[str] — patch_signature strings
    "stagnation_count": 0     # int
}
```

**Example values from real run** (paired8, core12_pin_typer_repair, benchmark_local/bench_math.py):

```
failures: [
  "Tests failed: benchmark_local/test_bench_math.py::test_double: assert 3 == 6; benchmark_local/test_bench_math.py::test_halve: assert 4 == 2"
]
attempted_patches: [
  "benchmark_local/bench_math.py||return n->return n // 2"
]
stagnation_count: 0
```

---

## 2. patch_signature Definition

**Location:** `editing/semantic_feedback.py` L295–303

```python
def patch_signature(prev: dict | None) -> str:
    old = (prev.get("old") or "").strip()
    new = (prev.get("new") or "").strip()
    file_path = (prev.get("file") or "").strip()
    symbol = (prev.get("symbol") or "").strip()
    return f"{file_path}|{symbol}|{old}->{new}"
```

**What it includes:**

| Field       | Included | Notes                                              |
|------------|----------|----------------------------------------------------|
| file path  | ✓        | e.g. `benchmark_local/bench_math.py`               |
| symbol     | ✓        | Often **empty** for text_sub (e.g. `\|` in `file\|symbol\|`) |
| old snippet| ✓        | Exact substring for text_sub                       |
| new snippet| ✓        | Replacement or inserted code                       |

**Classification: (A) output-level memory (patch)**

The signature is a canonical representation of the **patch output**: file + optional symbol + old->new. It does **not** represent “what the model intended to change” (e.g. “fix halve()”), only the literal diff applied.

---

## 3. Retry Prompt Analysis

**Location:** `format_stateful_feedback_for_retry` in `editing/semantic_feedback.py` L211–234

**What the model sees on retry:**
```
FAILURE_STATE:
- Known failures:
  - Tests failed: benchmark_local/test_bench_math.py::test_double: assert 3 == 6; ...
- Previous attempts (signatures):
  - benchmark_local/bench_math.py||return n->return n // 2
- Stagnation count: 0

REQUIREMENT:
- You MUST produce a patch that is different from previous attempts.
- You MUST address at least one of the known failures.
- Avoid repeating previously attempted changes.
```

**Does FAILURE_STATE include patch signatures only?**  
**Yes.** Only the signature string (truncated to 80 chars) is shown. No structured `{file, symbol, old, new}` or prose description.

**Does it include a description of what part of code was modified?**  
**No.** No “modified halve()” or “edited lines 10–12” or “changed function double”.

**Can the model infer WHAT WAS TRIED from this input?**

**Answer: NO** (with evidence)

- The model sees an opaque string: `benchmark_local/bench_math.py||return n->return n // 2`.
- Symbol is empty (`||`), so it is not told which function was edited.
- `return n` can appear in multiple functions (e.g. `double` and `halve`); the model does not know which one was targeted.
- In the real trace, the model repeated the same patch after seeing this, so the current format did not enable avoidance.
- The signature format is meant for exact equality checks, not for human/LLM interpretation of intent or location.

---

## 4. Structural Enforcement

**Location:** `check_structural_improvement` in `editing/semantic_feedback.py` L344–386

**What is compared:**  
`patch_signature(new_patch)` vs `patch_signature(previous_patch)` and vs `attempted_patches` (list of signatures).

**Notion of region / symbol / span / edit location?**  
**No.**

- The only comparison is `new_sig in attempted_patches` or `new_sig == old_sig`.
- `binding` (file/symbol) is used only for “same target” validation (wrong_target_file, wrong_target_symbol), not for repeat detection.
- There is no use of line range, AST node, or region.

**Conclusion:** Enforcement is at **(A) patch level** — exact equality of `file|symbol|old->new` strings. No action-level notion such as “already edited function X” or “already tried this region”.

---

## 5. Identified Gap

**1. What the system currently remembers:**
- Exact patch strings (`file_path|symbol|old->new`)
- Failure explanations (e.g. test failure messages)

**2. What the system does NOT remember:**
- Which function/symbol was modified (when symbol is empty)
- Line range or span
- Human-readable summary of “what was tried” (e.g. “modified halve() to use n//2”)
- Any notion of “edit location” separate from the literal old/new strings

**3. Why this causes repetition:**
- Patch-level memory only says “don’t output this exact string again”.
- It does not communicate “you already tried fixing halve(); try a different function or different strategy”.
- The model can re-derive the same patch from the same reasoning (e.g. “halve needs n//2”) and output identical text.
- When symbol is empty, the model cannot map `return n->return n // 2` to “that was in halve()”, so it may reuse that change in the same place or fail to try a different location.
- “Produce a different patch” is underspecified: the model lacks a clear notion of *what* must differ (location vs content vs both).

---

## 6. Minimal Data Needed to Fix It

**Proposed additions (data only, no implementation):**

| Addition            | Purpose |
|---------------------|---------|
| **modified_symbol** | Function/class/symbol name actually targeted, when known (from binding or patch). |
| **modified_span**   | Line range `(start, end)` or “in function X” for text_sub. |
| **edit_location_summary** | Short string like “in halve(): return n -> return n // 2” for display in FAILURE_STATE. |

**Examples:**
- `modified_symbol`: `"halve"` — “we edited halve()”.
- `modified_span`: `(7, 9)` — “lines 7–9”.
- `edit_location_summary`: `"halve(): return n -> return n // 2"` — human/LLM-readable description of what was tried.

**Constraint:** Only add data. Reuse existing flows (e.g. `extract_previous_patch` already has `symbol`; `edit_binding` has `file` and `symbol`). The gap is that symbol is often empty and we never surface a location summary to the retry prompt.
