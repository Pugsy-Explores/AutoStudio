# Patch Grounding Failure — System Audit

**Goal:** Determine whether patch failures are caused by **state inconsistency** (stale file) or **generation contract mismatch** (patch not grounded to evidence).

---

## Inputs

- `patch_debug` (from `patch_validation_debug` in context)
- PATCH VALIDATION TRACE (from execution_loop logs)

---

## Audit Steps

### Step 1 — Check State Consistency

**Inspect:** `file_contains_old_snippet`

| Value | Meaning |
|-------|---------|
| `FALSE` | Failure caused by **stale file state** (file changed since evidence/patch was generated) |
| `TRUE` | Proceed to Step 2 |
| `null` | Cannot evaluate (non-text_sub patch or missing data) |

---

### Step 2 — Check Patch Anchoring

**Compare:**
- `OLD_SNIPPET` — from generated patch (`patch.old` for text_sub)
- `evidence_span` — from EDIT_BINDING (concatenated evidence from ranked_context)

**Evaluate:** Exact match required (verbatim). `old_snippet` must appear in `evidence_span`.

| `snippet_match` | Meaning |
|-----------------|---------|
| `TRUE` | Patch is grounded to evidence |
| `FALSE` | Patch targets text outside evidence (model hallucinated or used wrong context) |

---

### Step 3 — Check Patch Locality

**Verify:** Patch modifies only the evidence span; no unrelated changes; no global rewrites.

| `locality` | Meaning |
|------------|---------|
| `valid` | `old_snippet` is contained in `evidence_span` — change is local |
| `invalid` | `old_snippet` not in evidence — patch targets region outside grounding |
| `unknown` | Cannot evaluate |

---

### Step 4 — Classify Failure

| Condition | `failure_type` |
|-----------|----------------|
| `file_contains_old_snippet = FALSE` | **STATE_INCONSISTENCY** |
| `file_contains_old_snippet = TRUE` AND `snippet_match = FALSE` | **GENERATION_CONTRACT_MISMATCH** |
| Otherwise | `null` (e.g. insert/symbol errors, multiple matches) |

---

## Output Schema

```json
{
  "failure_type": "STATE_INCONSISTENCY" | "GENERATION_CONTRACT_MISMATCH" | null,
  "evidence": {
    "snippet_match": true | false | null,
    "locality": "valid" | "invalid" | "unknown",
    "file_contains_old_snippet": true | false | null,
    "reason": "text_sub old snippet not found" | "...",
    "old_snippet": "return a * b + 1",
    "evidence_span": "def multiply(...)..."
  }
}
```

---

## Where to Find patch_debug

- **Eval output:** `check_retrieval_quality.py` writes `patch_debug` to task outcome (from `patch_validation_debug`)
- **Logs:** `[patch_debug] reason=... old_present=... snippet_match=... locality=... failure_type=...`

---

## Mitigations

| Failure Type | Mitigation |
|--------------|------------|
| **STATE_INCONSISTENCY** | Ensure `full_content` in edit proposal always reflects current on-disk state; avoid mixing stale evidence with fresh file reads; re-read file before retry |
| **GENERATION_CONTRACT_MISMATCH** | Strengthen prompt: require model to copy exact text from evidence; pass `evidence_span` more explicitly as the sole source for `old` |
