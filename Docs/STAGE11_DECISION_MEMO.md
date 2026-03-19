# Stage 11 — Architecture Decision Memo

**Type:** Principal Engineer Decision Memo  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Precondition:** Stage 10 closed — REPLAN parent policy on non-compat two-phase path; `_build_replan_phase` in `plan_resolver.py`; shared RETRY/REPLAN budget; traces `phase_replanned` / `phase_replan_failed`; `attempt_history` may include `plan_id`; proof **203** tests on hierarchical slice (`test_parent_plan_schema` + `test_run_hierarchical_compatibility` + `test_two_phase_execution`). See **`Docs/STAGE10_CLOSEOUT_REPORT.md`**.  
**Status:** Decision memo only. No code change implied by this document.

---

## 1. Repo Stage Map (Stages 1–10 Complete)

| Repo stage | Status | What shipped | Primary touchpoints |
|------------|--------|--------------|---------------------|
| **Stage 1** | Complete | `parent_plan.py` schemas; `get_parent_plan`; `run_hierarchical` compat → `run_deterministic` | `parent_plan.py`, `plan_resolver.py`, `deterministic_runner.py` |
| **Stage 2** | Complete | `_is_two_phase_docs_code_intent`; `_build_two_phase_parent_plan`; phase loop; context handoff; `GoalEvaluator.evaluate_with_reason(phase_subgoal=...)` | `plan_resolver.py`, `deterministic_runner.py`, `goal_evaluator.py` |
| **Stage 3** | Complete | Phase validation enforcement; trace/reporting for parent retry | `deterministic_runner.py` |
| **Stage 4** | Complete | Real parent retry (`CONTINUE` / `RETRY` / `STOP`); `errors_encountered_merged` per phase | `deterministic_runner.py` |
| **Stage 5** | Complete | `attempt_history`; top-level `attempts_total` / `retries_used`; lock extension | `deterministic_runner.py`, `hierarchical_test_locks.py` |
| **Stage 6** | Complete | `_derive_phase_subgoals` connector expansion; `two_phase_near_miss` trace | `plan_resolver.py` |
| **Stage 7** | Complete | `_coerce_max_parent_retries`; config-driven `max_parent_retries` for shipped two-phase plans | `config/agent_config.py`, `plan_resolver.py` |
| **Stage 8** | Complete | Per-phase budgets `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0` / `_PHASE_1` | `config/agent_config.py`, `plan_resolver.py` |
| **Stage 9** | Complete | Hold-and-measure **docs only**: `REPLAN_MEASUREMENT_CRITERIA.md`, `REPLAN_PRECODING_DECISIONS.md` | `Docs/*` |
| **Stage 10** | Complete | **`REPLAN`** in `_parent_policy_decision_after_phase_attempt`; consecutive same-`failure_class` → replan; `_build_replan_phase`; `phase_replanned` / `phase_replan_failed`; optional `plan_id` on attempt rows | `deterministic_runner.py`, `plan_resolver.py`, `tests/test_two_phase_execution.py` |

### 1.1 Roadmap vs Repo Warning (required)

**`Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §2 (“Stage 3 — Parent Policy and Escalation”)** describes a **full** parent policy including **`REQUEST_CLARIFICATION`**.

**Repo state after Stage 10:**

| Policy outcome | In `deterministic_runner.py` (non-compat hierarchical path) |
|----------------|--------------------------------------------------------------|
| `CONTINUE` | Yes — phase success, proceed |
| `RETRY` | Yes — same plan, another attempt if budget |
| `REPLAN` | Yes — Stage 10; `decision_reason == "replan_scheduled"` when consecutive same `failure_class` |
| `STOP` | Yes — terminal failure / no budget |
| **`REQUEST_CLARIFICATION`** | **No** — not implemented; no branch emits it; callers cannot request structured user clarification via `loop_output` |

**Roadmap §95–101 (“Stage 4 — Broader Decomposition Patterns”)** — **3+ phases** — remains a **separate re-approval gate**. Repo still enforces `len(phases) != 2` → `NotImplementedError` on non-compat path.

Do **not** use roadmap stage numbers as repo stage labels in review or scheduling.

---

## 2. Locked Invariants (Stage 11 Must Preserve)

Any Stage 11 **implementation** slice that violates one of these should be rejected unless explicitly re-approved as a broader architecture change. (Stage 11 **recommended** slice is **docs-only** — see §6 — and touches **none** of these at runtime.)

| ID | Invariant | Enforcement / notes |
|----|-----------|---------------------|
| **L1** | **Compat exact delegation:** `run_hierarchical(..., compatibility_mode=True)` returns **exactly** `run_deterministic`’s `(state, loop_output)` — same object identity for `loop_output` where tests assert it | `run_hierarchical` early return; `test_run_hierarchical_compatibility.py` |
| **L2** | **No hierarchical-only top-level keys on compat `loop_output`** | `HIERARCHICAL_LOOP_OUTPUT_KEYS` + `_PHASE_RESULT_FIELD_NAMES` in `tests/hierarchical_test_locks.py`; `assert_compat_loop_output_has_no_hierarchical_keys` |
| **L3** | **`loop_output["phase_count"]` == `len(phase_results)` == executed phases only** | `_build_hierarchical_loop_output` |
| **L4** | **One final `phase_result` row per phase**; `attempt_count` = total parent attempts for that phase | Stage 4/5/10 tests |
| **L5** | **Handoff** (`prior_phase_ranked_context`, `prior_phase_retrieved_symbols`, `prior_phase_files`) built only from **final successful** phase result after retries/replan | `_build_phase_context_handoff` |
| **L6** | **`len(phases) != 2`** on non-compat path → `NotImplementedError` unless re-approved | `run_hierarchical` |
| **L7** | **`tests/hierarchical_test_locks.py` is contract authority** for new top-level hierarchical keys; extending it is a **scope smell** unless explicitly planned | Frozen by policy unless contract expansion approved |

---

## 3. Post-Stage-10 Facts (Repo-Grounded)

- **`_parent_policy_decision_after_phase_attempt`** (`deterministic_runner.py`) returns **`("REPLAN", "replan_scheduled")`** when `attempt_number < max_attempts`, the phase failed, and **`failure_class` equals `previous_attempt_failure_class`** (same phase, consecutive failures); otherwise **`RETRY`** if budget remains; else terminal **`STOP`** via `_parent_policy_decision_with_reason`.
- **REPLAN shares `max_parent_retries`** with RETRY — no separate replan counter in code.
- **`_build_replan_phase`** lives in **`plan_resolver.py`**; it does **not** call `get_parent_plan` or `run_hierarchical` (Stage 10 guard).
- **Traces:** `phase_replanned` on successful plan substitution; `phase_replan_failed` on exception or malformed plan → terminal STOP for that phase.
- **`prior_phase_ranked_context`** / related keys are still **injected** into Phase 1 `AgentState.context` via `_build_phase_agent_state`; **consumption in retrieval/ranking** is unchanged — execution pipeline does not merge them into ranked retrieval (consistent with roadmap §5.5 / precoding docs).
- **Production/staging analytics on REPLAN** — rates, false positives, residual `STOP` reasons after REPLAN — are **not** available in this memo’s evidence set; Stage 10 landed without a mandatory observation window in code.

---

## 4. Candidate Evaluation

### Candidate A — `REQUEST_CLARIFICATION` Parent-Policy Outcome

#### What problem it solves

After **RETRY**, **REPLAN**, and budget exhaustion, hierarchical runs still end as **`STOP`** with `parent_goal_met: False` and `errors_encountered` / synthetic phase markers. That is **indistinguishable** from “hard failure” for many callers. **REQUEST_CLARIFICATION** would signal: *the system cannot proceed without human input* (ambiguous instruction, missing disambiguation, policy boundary) — a **product-visible** terminal class, not a silent catch-all.

#### Exact files that would need to change

- **`agent/orchestrator/deterministic_runner.py`** — **Mandatory.** Extend parent policy to return `REQUEST_CLARIFICATION` under **defined** triggers; branch the per-phase loop and/or aggregation so the run terminates with a clarification payload. Touches the **same** high-risk surface as Stages 4 and 10 (`run_hierarchical` inner loop, `_build_hierarchical_loop_output`).
- **`agent/orchestrator/parent_plan.py`** or **`agent/orchestrator/`** new module — **clarification payload schema** (fields, versioning, max size).
- **Every consumer of `run_hierarchical`** that inspects `loop_output` — **caller contract** updates.
- **`tests/test_two_phase_execution.py`** — invariants for clarification vs STOP.
- **`tests/hierarchical_test_locks.py`** — **if** a new **top-level** hierarchical key is required (e.g. `clarification_requested`, `clarification_payload`), **L7** forces an update here — **explicit contract expansion**.

**Explicit:** This candidate **cannot** be “just a trace change.” It is **policy + output shape + likely callers**.

#### Blast radius

**Very high.** Product integration, dashboard semantics, and **possible** `hierarchical_test_locks.py` expansion. Mis-implementing clarification as “another STOP with extra trace noise” wastes the feature; implementing it as **new top-level keys** without coordinated callers **breaks** consumers.

#### New invariants required (must be precoded before implementation)

- Clarification is **terminal** — no further retries for that run (unless explicitly re-scoped).
- **Never** on compat path.
- Whether clarification attaches when **partial** phases succeeded vs only when **zero** phases succeeded.
- **`phase_count` / `phase_results` length** rules — must stay consistent with **L3** / **L4**.
- Schema versioning for any payload stored on `loop_output` or nested under `phase_results`.

#### Compatibility / caller-contract risks

- **L1 / L2:** Compat must remain key-clean; clarification must be **hierarchical-only**.
- Any caller assuming “failure == STOP” must be updated **in lockstep** or will mis-handle UX and retries upstream.

#### Observability impact

- New `parent_policy_decision.decision` value; likely **`clarification_requested`** or equivalent **trace** and/or **loop_output** fields.
- Auditable but **high ceremony**; dashboards must be updated.

#### Why now

Only when **trace evidence** shows a **stable, high-rate** class of terminal failures that are **not** fixable by better plans (REPLAN) or better retrieval (Candidate B) — e.g. systematic ambiguity after exhausted budgets.

#### Why not now (default)

Stage 10 **just** added **REPLAN** and more branch complexity in `deterministic_runner.py`. Shipping **REQUEST_CLARIFICATION** immediately **layers** caller-contract work on top of **unmeasured** REPLAN behavior. Per **`STAGE7_DECISION_MEMO.md`** / roadmap ordering, clarification was previously gated behind **replan + budget** semantics; repo now has REPLAN but **no** field evidence that clarification is the next bottleneck.

**Explicit:** This candidate **likely requires** `deterministic_runner.py`, a **clarification payload schema**, **caller handling updates**, and **likely `hierarchical_test_locks.py`** if a new top-level output key is introduced.

---

### Candidate B — Retrieval Use of `prior_phase_ranked_context` / Handoff Keys

#### What problem it solves

Phase 1 **`execution_loop`** builds retrieval from **Phase 1** state. Handoff keys (**`prior_phase_ranked_context`**, **`prior_phase_retrieved_symbols`**, **`prior_phase_files`**) are merged into `AgentState.context` in `_build_phase_agent_state` but — per existing architecture docs — are **not** fed into the same ranking/candidate path as Phase 1’s native retrieval. **True two-phase quality** often requires Phase 1 search/ranking to **condition** on Phase 0 artifacts.

#### Exact frozen files that would need to change

Per **`HIERARCHICAL_PHASED_ORCHESTRATION_PRECODING_DECISIONS.md`** and roadmap §5.5, these modules are **frozen** for casual edits:

| File | Role in merge |
|------|----------------|
| **`agent/orchestrator/execution_loop.py`** | Primary candidate for bounded merge of prior-phase context into retrieval or pre-ranking |
| **`agent/execution/step_dispatcher.py`** | Alternative or secondary injection point depending on pipeline architecture |
| **`agent/orchestrator/replanner.py`** | Only if replanning must observe merged context — usually **not** first slice |

**No** change to **`run_deterministic`** or compat path if merge is hierarchical-only and gated on presence of handoff keys.

#### Blast radius

**High.** Affects **every** hierarchical Phase 1 execution path; risks **double-counting** context, **lane** confusion (docs vs code artifacts), **non-deterministic** ranking if merge order is unspecified, and **test matrix explosion** (two-phase × retrieval × validation).

#### Need for a written merge spec

**Mandatory before code.** Must specify: **where** in the pipeline merge happens; **dedup** vs Phase 1 native candidates; **caps** (tie to `MAX_CONTEXT_CHARS` / existing pruning); interaction with **PhaseValidationContract** (`require_ranked_context`, `min_candidates`); **determinism** (stable ordering). **No ad-hoc** `execution_loop` edits.

#### Compatibility risks

- **Compat:** unchanged (no handoff).
- **Single-phase / deterministic baseline:** merge must **not** activate without handoff keys.

#### Observability needs

- Merge **provenance** in trace (counts merged, truncated, source lane) — otherwise production debugging is guesswork.

#### Why now

If measurement shows Phase 1 **systematically** under-ranks relevant code after **good** Phase 0 docs context — **after** parent-policy behavior (RETRY/REPLAN) is understood in production.

#### Why not now (default)

Touches **frozen** execution modules; requires **merge design** unrelated to whether **REQUEST_CLARIFICATION** exists. Doing B in the same release train as **unmeasured** REPLAN **confounds** attribution (“did REPLAN fix it, or did merge fix it?”).

---

### Candidate C — Hold-and-Measure After Stage 10 REPLAN

#### What problem it solves

Stage 10 changed **runtime behavior** (new policy branch, replan traces, `plan_id` in history). **Without** an observation discipline, the next PR will be **guess-driven**: either expand **caller contracts** (A) or **frozen retrieval** (B) without knowing whether REPLAN already moved the failure distribution. **Candidate C** institutionalizes **evidence** before Stage 12 code.

#### What gets delivered without changing execution semantics (default)

- **Documentation:** clarification **measurement criteria**; optional **retrieval merge design questions** (no implementation); **proof baseline** pointer; **hold-expiry** rules for Stage 12.
- **Optional:** additive **trace payloads** in `deterministic_runner.py` **only** if existing `phase_completed` / `parent_policy_decision` / `phase_replanned` events are **proven insufficient** for dashboards — **not** the default Stage 11 slice.

#### Exact files that may change

| File | Role |
|------|------|
| **`Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`** (new) | When REQUEST_CLARIFICATION would be justified; trace fields; false-positive exclusions; anti-patterns (timeouts mistaken for ambiguity) |
| **`Docs/STAGE12_PRECODING_DECISIONS.md`** (new, **conditional**) | Written **when hold expires** if Stage 12 is clarification — locks payload, compat, **L7** impact, trace names. If Stage 12 is retrieval-merge instead, **separate** precoding doc — do not assume clarification. |
| **`Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md`** (optional) | Open questions only — merge locus, caps, dedup, determinism |
| **`deterministic_runner.py`** | **Only if** trace gap proven — additive payload fields, **no** policy change |

**Default:** **docs only** — **zero** production files.

#### Blast radius

**Zero** at runtime for docs-only Stage 11.

#### New invariants

**None** for docs-only deliverables.

#### Why this is the best immediate next step after Stage 10

1. **REPLAN is retry-loop surgery** — the same file that would host **REQUEST_CLARIFICATION** was **just** modified. Stacking **A** without measurement is **reckless**.
2. **Retrieval merge (B)** is a **different subsystem** — frozen files, merge spec — and should not compete for attention with stabilizing REPLAN metrics.
3. **Stage 9** already established the precedent: **measure before the next expensive gate** (`REPLAN_MEASUREMENT_CRITERIA.md`). Stage 11 repeats that pattern **after** REPLAN ships: **measure REPLAN before clarification or retrieval code**.

---

## 5. Decision Standards

| Constraint | Implication for Stage 11 |
|------------|--------------------------|
| Smallest blast radius | **C** ≫ **A** or **B** for this window |
| Preserve **L1**, **L2**, **L7** | **A** almost certainly pressures **L7** if product needs visible clarification on `loop_output` |
| Avoid `deterministic_runner.py` churn without evidence | Prefer **C** before **A** |
| Frozen execution modules | **B** needs explicit approval + merge spec — not a Stage 11 default |
| Prefer extension over replacement | **C** extends **process** (docs, gates), not a second execution engine |

---

## 6. Recommendation

### 6.1 Chosen next slice — **Candidate C: Hold-and-Measure After Stage 10 REPLAN**

**Rationale (blunt):**

- **A** is **caller-contract** work with **likely `hierarchical_test_locks.py`** impact — the widest blast radius and the wrong layer to rush while REPLAN traces are **cold**.
- **B** is **correct** for end-user quality but is **not** a parent-policy slice — it needs a **merge design** and **frozen-file** approval. Doing B now **confounds** attribution with REPLAN.
- **C** is the only candidate that **does not** bet the architecture on unobserved REPLAN behavior.

### 6.2 Stage 11 concrete deliverables (documentation-first)

1. **`Docs/REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`**  
   - Purpose; **hold-expiry** signals (e.g. rate of terminal `STOP` after exhausted RETRY/REPLAN with specific `failure_class` / error markers); **trace events used** (`parent_policy_decision`, `phase_replanned`, `phase_replan_failed`, `phase_completed`); **false positives** (infra flake, `timeout`, `limit_exceeded`); **explicit gate** — “this doc authorizes Stage 12 **clarification precoding**, not implementation.”

2. **`Docs/STAGE12_PRECODING_DECISIONS.md`** — create **when** Stage 11 hold expires **and** product/architecture chooses **REQUEST_CLARIFICATION** for Stage 12. Locks: payload schema, whether new top-level keys are allowed, **L7** checklist, terminal semantics, trace event set. **If** Stage 12 is **retrieval merge** instead, author **`Docs/STAGE12_RETRIEVAL_MERGE_PRECODING_DECISIONS.md`** (or similar) — **do not** reuse clarification precoding for merge.

3. **Optional:** **`Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md`** — questions only; **no** code.

4. **Record proof baseline after Stage 10** — hierarchical slice **203** passed (see **`STAGE10_CLOSEOUT_REPORT.md`**); re-run proof commands after checkout to detect drift.

### 6.3 Hold-expiry conditions (Stage 11 → Stage 12)

Stage 11 documentation “hold” ends when **any** of:

1. **Observation window** (e.g. ≥7 days in target environment) completes **and** dashboards show **stable** REPLAN/clarification-relevant metrics **or** a **documented** inability to observe (triggers smaller sandbox experiment — still **not** automatic code).
2. **Explicit architecture / product override** — written justification; must still document **L1–L7** impact for any Stage 12 implementation.

Stage 12 **choice** (clarification vs retrieval merge vs other) is **not** predetermined by Stage 11 — Stage 11 only ensures **evidence or explicit override** exists.

### 6.4 Rollback

Stage 11 as recommended is **docs-only** → **no runtime rollback**. If optional trace additions were ever merged under a different decision, rollback is revert those commits — **not** related to this memo’s default.

### 6.5 Smallest viable implementation scope (Stage 11 if Candidate C is approved)

| Step | Action |
|------|--------|
| 1 | Add **`REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`** per §6.2 |
| 2 | Link from **`STAGE10_CLOSEOUT_REPORT.md`** or **MASTER** readme if project maintains one — **optional**; do not block on it |
| 3 | Record **203** / **180** proof baselines in team notes or the new doc’s footer |
| 4 | **Do not** implement **A** or **B** in Stage 11 |

---

## 7. Do Not Do Yet (Stage 11 Scope Guards)

| Item | Reason |
|------|--------|
| **REQUEST_CLARIFICATION implementation** | **L7** / caller risk; no measurement |
| **Retrieval merge implementation** | Frozen modules + merge spec |
| **≥ 3 phases** | **L6**; roadmap gate |
| **Widen `_is_two_phase_docs_code_intent`** | Use `two_phase_near_miss` + traces first |
| **New top-level hierarchical `loop_output` keys** | **L2** / **L7** |
| **Edit `hierarchical_test_locks.py`** | Unless separate **contract expansion** approval |
| **Change `run_deterministic`, `execution_loop.py`, `replanner.py`, `step_dispatcher.py`** | Out of scope for Stage 11 default |

---

## 8. Relation to Prior Memos and Docs

- **`STAGE9_DECISION_MEMO.md`** recommended **hold-and-measure before REPLAN**; Stage 10 implemented REPLAN. Stage 11 applies the **same engineering discipline after REPLAN**: **evidence before the next contract-heavy or frozen-file slice**.
- **`STAGE8_DECISION_MEMO.md` / `STAGE7_DECISION_MEMO.md`** — deferred **REQUEST_CLARIFICATION** and **retrieval** as larger than config/planning slices; that ordering **still holds** post–Stage 10.
- **`REPLAN_MEASUREMENT_CRITERIA.md`** / **`REPLAN_PRECODING_DECISIONS.md`** — template for **criteria + precoding** before big code; Stage 11’s **`REQUEST_CLARIFICATION_MEASUREMENT_CRITERIA.md`** mirrors that pattern for the **next** candidate.
- **`STAGE10_CLOSEOUT_REPORT.md`** — authoritative **what shipped** for Stage 10; Stage 11 does not reopen Stage 10 code without new evidence or explicit override.

---

*End of Stage 11 decision memo.*
