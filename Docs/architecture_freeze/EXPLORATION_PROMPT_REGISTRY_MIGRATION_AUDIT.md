# Exploration Stage — Prompt Registry Audit & Migration Plan

**Status:** Architecture reference (audit + planned refactor).  
**Scope:** Agent V2 exploration stage prompt management.  
**Constraints (when implementing):** Do not change prompt semantics in the first pass; do not alter exploration control logic; prefer registry extension over new parallel abstractions.

---

## 1. Current Prompt Map

| Component | File | Function(s) | How prompt is built | LLM call | Centralized? |
|-----------|------|---------------|---------------------|----------|----------------|
| **QueryIntentParser** | `agent_v2/exploration/query_intent_parser.py` | `parse()` | Single f-string embedding `{instruction}` | `self._llm_generate(prompt)` | Hardcoded |
| **ExplorationScoper** | `agent_v2/exploration/exploration_scoper.py` | `scope()`, `_build_prompt()` | f-string + `json.dumps(payload)` for candidates | `self._llm_generate(prompt)` | Hardcoded |
| **CandidateSelector** | `agent_v2/exploration/candidate_selector.py` | `select()`, `select_batch()` | Two paths: (1) short concatenated string + instruction + `json.dumps(payload)`; (2) long f-string with instruction, optional explored block, payload, limit | `self._llm_generate(prompt)` | Hardcoded (duplicate selector semantics) |
| **UnderstandingAnalyzer** | `agent_v2/exploration/understanding_analyzer.py` | `analyze()` | f-string with instruction, file_path, snippet | `self._llm_generate(prompt)` | Hardcoded |
| **GraphExpander** | `agent_v2/exploration/graph_expander.py` | `expand()` | No LLM prompt — graph via `fetch_graph`, else SEARCH step with string `f"{symbol} callers callees definition"` | N/A (dispatcher tools only) | N/A |
| **InspectionReader** / **read_router** | `inspection_reader.py`, `read_router.py` | — | No prompts | No LLM | N/A |

**Wiring:** `ExplorationRunner` injects the same `llm_generate_fn` into parser, scoper, selector, and analyzer (`agent_v2/runtime/exploration_runner.py`).

---

## 2. Prompt Registry Audit

**Implementation:** `agent/prompt_system/registry.py`, `agent/prompt_system/loader.py`.

**Storage:**

- Versioned: `agent/prompt_versions/{name}/{version}.yaml` (e.g. `v1.yaml`).
- Legacy: `agent/prompts/{file_stem}.yaml` via `_LEGACY_MAP` in `loader.py`.

**Retrieval:**

- `PromptRegistry.get(name, version="latest", variables=None)` → `PromptTemplate` (`load_prompt`).
- `get_instructions(name, ...)` → string only.
- `version="latest"` is normalized to `v1` in `load_prompt`.

**Keys:** `_DEFAULT_REGISTRY` lists logical names (`planner`, `router`, `react_action`, …). Exploration names are not present.

**Model-specific variants:** Not supported today. The registry maps logical name → `ModelType` (SMALL vs REASONING) for routing hints (`get_model_type`), not per model id (e.g. `gpt-4` vs `claude`). Resolution is by prompt name + version string, not `model_name`.

---

## 3. LLM Call Path

| Topic | Detail |
|--------|--------|
| **Injection** | `ExplorationRunner(..., llm_generate_fn=...)` from `AgentRuntime` / `create_runtime()` / `create_exploration_runner()` in `agent_v2/runtime/bootstrap.py`. |
| **Implementation** | `lambda prompt: call_reasoning_model(prompt, task_name="EXPLORATION_V2")`. |
| **Model selection** | `call_reasoning_model` (`agent/models/model_client.py`) uses `task_name` → `models_config` `task_models[task_name]` and `task_params` — not prompt registry keys. |
| **Prompt registry** | Exploration path does not call `get_registry()` or pass `prompt_name` into `call_reasoning_model` (that parameter exists for post-call validation / guardrails). |
| **Bypass** | Full exploration instructions are built in Python and passed as the user message to the reasoning model; no PromptRegistry load on this path. |

**Summary:** Model name is determined by task `EXPLORATION_V2`; prompt text is inline in `agent_v2/exploration/*.py`.

---

## 4. Gaps

1. All exploration LLM prompts are inline — no YAML, no `_DEFAULT_REGISTRY` entries, no `test_prompt_regression` coverage for exploration.
2. **CandidateSelector** has two different prompts (`select` vs `select_batch`) — one short, one long; a single key `exploration.selector` is ambiguous unless split (e.g. `exploration.selector.single` / `.batch`) or documented as one template with a mode.
3. No reuse — repeated ROLE/TASK/CONSTRAINTS/JSON patterns across files.
4. No model-specific prompt text — switching endpoints via `task_models` does not change instructions; no `prompt_key + model_name → text` resolution.
5. Prompt selection is mixed with execution (`llm_generate`); registry centralizes selection only.
6. **GraphExpander** — no registry entry needed for LLM; SEARCH query string is deterministic, not an LLM prompt.
7. **call_reasoning_model** supports `prompt_name` for guardrails/validation — exploration does not use it yet; can wire after registry migration.

---

## 5. Migration Plan (Minimal, Safe)

**Principles:** Copy exact current strings into YAML; preserve composed message equivalence; extend registry only for model-suffix resolution.

### Step 1 — Centralize Prompts

- Add versioned YAML under e.g. `agent/prompt_versions/exploration.query_intent_parser/v1.yaml` (and similarly for scoper, selector, analyzer).
- For dynamic parts, use `str.format` / `format_map` placeholders in YAML (`{instruction}`, `{candidates_json}`, etc.) consistent with `loader.load_prompt`, or store a static prefix in YAML and keep concatenation in code for the lowest-risk first pass.
- Register names in `_DEFAULT_REGISTRY` (and `_LEGACY_MAP` if using flat `agent/prompts/*.yaml`).
- **Selector:** Prefer two registry keys (`exploration.selector.single` and `exploration.selector.batch`) unless unifying code paths.

**Suggested logical keys:**

- `exploration.query_intent_parser`
- `exploration.scoper`
- `exploration.selector` (or `.single` / `.batch`)
- `exploration.analyzer`
- `exploration.graph_expander` — N/A (no LLM); optional constant/docs only.

### Step 2 — Replace Inline Usage

- In each component, use `get_registry().get_instructions("exploration....", variables={...})` or equivalent.
- Preserve byte-for-byte output for the default path (golden test recommended).

### Step 3 — Model-Based Versioning

- Extend `load_prompt` / `PromptRegistry.get` with optional `model_name: str | None`.
- **Resolution order:**  
  `prompt_versions/{name}/models/{normalized_model}/v1.yaml` →  
  `prompt_versions/{name}/v1.yaml` →  
  legacy map.  
  Normalize model id for filenames (lowercase, safe characters).  
- Fallback: default file at name root (`v1.yaml`).

### Step 4 — Integration with LLM Config

- In `bootstrap.py`, resolve the model id used for task `EXPLORATION_V2` (same source as `call_reasoning_model`).
- Pass `model_name` into exploration components (constructor injection recommended) so prompt composition calls `get(..., model_name=resolved_model)` without changing `call_reasoning_model`’s task routing.
- Optionally pass `prompt_name` to `call_reasoning_model` for guardrails once templates are registry-backed.

**Note:** Today `llm_generate` is `Callable[[str], str]` — it carries no model name. Minimal approach: inject `exploration_model_name: str` at `ExplorationRunner` / engine construction from bootstrap.

---

## 6. Code Changes Required (Files + Functions)

| File | Change |
|------|--------|
| `agent/prompt_system/registry.py` | Register exploration keys in `_DEFAULT_REGISTRY`; optional `get(..., model_name=...)` delegation. |
| `agent/prompt_system/loader.py` | Model-suffix resolution in `load_prompt` / `load_from_versioned`. |
| `agent/prompt_versions/...` | New YAML trees per exploration key (+ optional `models/<id>/v1.yaml`). |
| `tests/test_prompt_regression.py` | Add exploration keys to `_PROMPT_NAMES`. |
| `agent_v2/exploration/query_intent_parser.py` | `parse()` — load template, format with `instruction`. |
| `agent_v2/exploration/exploration_scoper.py` | `_build_prompt()` — load template, format with instruction + payload JSON. |
| `agent_v2/exploration/candidate_selector.py` | `select()`, `select_batch()` — two templates. |
| `agent_v2/exploration/understanding_analyzer.py` | `analyze()` — load template, format with instruction, path, snippet. |
| `agent_v2/runtime/exploration_runner.py` | Pass `model_name` (or helper) into components if using constructor injection. |
| `agent_v2/runtime/bootstrap.py` | Resolve model name for `EXPLORATION_V2`; pass into `ExplorationRunner` / `create_exploration_runner`. |
| `agent_v2/runtime/runtime.py` | Thread `exploration_llm_fn` + optional `exploration_model_name` into `ExplorationRunner`. |

**Out of scope for LLM registry:** `graph_expander.py` (no LLM prompt).

---

## 7. Target State

| From | To |
|------|-----|
| Inline prompts + no versioning | Centralized prompts + model-aware selection |
| `task_name` only for endpoint | Same task config drives both endpoint and prompt file variant |

---

## Related Documents

- `Docs/architecture_freeze/EXPLORATION_PIPELINE_ARCHITECTURE_AUDIT.md`
- `Docs/architecture_freeze/PHASE_12_6_F_EXPLORATION_SCOPER.md`
- `Docs/architecture_freeze/PROMPT_DESIGN_SPECIFICATION.md`
