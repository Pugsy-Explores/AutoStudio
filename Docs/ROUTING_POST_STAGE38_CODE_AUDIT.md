# Routing stack post–Stage 38 — code-first architectural audit

**Method:** Read-only inspection of `agent/routing/`, `agent/orchestrator/plan_resolver.py`, and the three named test files. No README/stage docs used as evidence.

**Stage 39 (implemented):** Production-honest contract: `PRODUCTION_EMITTABLE_PRIMARY_INTENTS`, `DEFERRED_PRIMARY_INTENTS`; VALIDATE explicitly deferred; `resolver_consumption` telemetry; production-path tests (`route_production_instruction` → `get_plan` with mocked legacy router). See `agent/routing/intent.py`, `agent/routing/README.md`, `Docs/ROUTING_ARCHITECTURE_REPORT.md`.

---

## 1. Executive summary

| Question | Verdict |
|----------|---------|
| **Unified in behavior or only in interfaces?** | **Partially unified.** Production has **one function** that returns `RoutedIntent` (`route_production_instruction`), and `plan_resolver` no longer duplicates docs/two-phase **string checks**. Behavior is still **not** a single semantic model: it is a **fixed priority chain** (deterministic docs → deterministic two-phase → legacy 5-way model router). The 8-way taxonomy exists in **`intent.py` + `simple_router.py`**, but **`simple_router` is not on the production path** — only tests. |
| **Biggest remaining architectural problem** | **Legacy `RouterDecision` vocabulary (5 categories) is still the only ML path**, while the product taxonomy advertises **DOC, VALIDATE, COMPOUND (non–docs-only), AMBIGUOUS semantics** that **production routing cannot emit** except via narrow rules (DOC/two-phase COMPOUND) or adapter mapping (GENERAL→AMBIGUOUS). **`VALIDATE` is dead in production emission** — see §8. |
| **Next highest-value routing stage** | **Expand the legacy router’s native outputs (or replace it) so SEARCH/EDIT/EXPLAIN/INFRA/DOC/VALIDATE/COMPOUND/AMBIGUOUS are expressible without lying in adapters** — *unless* you explicitly accept planner-only handling for validate/compound; then the honest next step is **document that** and **stop implying `PLAN_SHAPE_SINGLE_STEP_VALIDATE` is reachable from production**. Evidence: `instruction_router.py` lines 14–15, `intent.py` `_LEGACY_TO_INTENT`, `plan_resolver.py` branches (no `INTENT_VALIDATE` arm). |
| **Acceptable for a general software AI assistant?** | **Usable but transitional.** Observability improved; split string logic in `plan_resolver` removed. Core limitation: **planner still absorbs anything that isn’t SEARCH/EXPLAIN/INFRA/DOC seed**, including **all validation intent** and **all “compound” except the one docs+code pattern**. That is **assistant-grade** only if the planner is trusted to disambiguate — routing is not doing that job yet. |

**Recommendation headline (see §15):** Expand native router categories / prompt **or** formally demote VALIDATE/COMPOUND in the contract — the code already demotes them in practice.

---

## 2. Production routing call graph

### 2.1 `get_plan()` — `plan_resolver.get_plan` (lines 147–350)

1. `route_production_instruction` **imported inside function** — `agent/routing/production_routing.py:route_production_instruction`  
2. `ri = routed_intent or route_production_instruction(instruction, ignore_two_phase=...)` — line 172–176  
3. If `ENABLE_INSTRUCTION_ROUTER` and `ri.primary_intent == COMPOUND` → set `routing_overridden_downstream` / reason — lines 178–183  
4. `_merge_routing_telemetry(ri, ...)` — lines 185–189  
5. `legacy_cat = _legacy_router_category_label(ri)` — line 191  
6. **Branch:** `not ENABLE_INSTRUCTION_ROUTER` → `plan()` only — lines 193–209  
7. **Branch:** `INTENT_DOC` **and** `suggested_plan_shape == PLAN_SHAPE_DOCS_SEED_LANE` → `_docs_seed_plan` — lines 212–236  
8. **Else** log `instruction_router` event — lines 238–250  
9. **Branch:** `INTENT_SEARCH` → single `SEARCH` step — 252–278  
10. **Branch:** `INTENT_EXPLAIN` → single `EXPLAIN` — 279–305  
11. **Branch:** `INTENT_INFRA` → single `INFRA` — 306–332  
12. **Fallback:** `plan()` — lines 334–350 (comment explicitly lists EDIT, VALIDATE, AMBIGUOUS, COMPOUND)

**Where decisions are made:**  
- **Primary:** `route_production_instruction` (unless `routed_intent` injected).  
- **Secondary overrides inside `get_plan`:** COMPOUND → planner + telemetry override; router disabled → planner; DOC requires **both** primary DOC **and** shape `docs_seed_lane` (production always sets both for docs path).

### 2.2 `get_parent_plan()` — lines 563–673 (file continues)

1. `ri = route_production_instruction(instruction)` — line 578  
2. `_merge_routing_telemetry(ri)` — line 579 (**duplicate merge** with what `get_plan` will do again)  
3. If `COMPOUND` **and** `suggested_plan_shape == PLAN_SHAPE_TWO_PHASE_DOCS_CODE` → `_build_two_phase_parent_plan` — lines 581–603  
4. On exception → `get_plan(..., ignore_two_phase=True)` + `make_compatibility_parent_plan` — lines 613–633  
5. Else near-miss logging using `is_two_phase_docs_code_intent` — lines 635–649  
6. Else `flat_plan = get_plan(..., routed_intent=ri)` — avoids second `route_production_instruction` — lines 651+

### 2.3 `route_production_instruction()` — `production_routing.py:48–139`

1. `ENABLE_INSTRUCTION_ROUTER` false → synthetic `AMBIGUOUS`, `matched_signals=("router_disabled",)` — lines 73–83  
2. `is_docs_artifact_intent(instruction)` → `DOC` — lines 85–96  
3. `not ignore_two_phase` and `is_two_phase_docs_code_intent(instruction)` → `COMPOUND` + `two_phase_docs_code` — lines 98–109  
4. Else `route_instruction(instruction)` — `instruction_router.py:63–114` — line 112  
5. Confidence check for categories in `_SHORT_CIRCUIT_ROUTER_CATEGORIES` — lines 32–32, 115–118  
6. If fallback → explicit `AMBIGUOUS` — lines 120–134  
7. Else `routed_intent_from_router_decision` — lines 136–138  

### 2.4 Docs intent — `docs_intent.py`

- `is_docs_artifact_intent` — lines 52–70  
- `is_two_phase_docs_code_intent` — lines 73–91 (calls docs-artifact first; **mutual exclusion**)

### 2.5 Legacy model router — `instruction_router.py`

- `route_instruction` → `RouterDecision(category, confidence)` — categories **only** from `ROUTER_CATEGORIES` — line 14  
- Optional `router_registry` — `router_registry.py` maps eval routers to same five categories — lines 11–31  

### 2.6 `RoutedIntent` consumption

- **`plan_resolver`** branches on **`primary_intent` only** for short-circuits (SEARCH/EXPLAIN/INFRA); DOC also requires **shape**.  
- **`suggested_plan_shape`** is **not** used in a switch; DOC path is the only shape-sensitive branch.  
- **`secondary_intents`, `decomposition_needed`, `clarification_needed`** — **telemetry / schema only** in production; **no consumer** in `plan_resolver` execution logic (grep: only `intent.py` defines them).

---

## 3. Routing taxonomy audit

| Intent | Represented in code | Production `route_production_instruction` can emit? | Legacy router emits natively? | `plan_resolver` maps to | First-class vs adapter | Downstream override |
|--------|---------------------|-----------------------------------------------------|-------------------------------|-------------------------|-------------------------|----------------------|
| **SEARCH** | `INTENT_SEARCH`; legacy `CODE_SEARCH` | **Yes** — via `routed_intent_from_router_decision` | **Yes** | Single `SEARCH` step — lines 252–278 | **First-class** on model path | Low conf on SEARCH → **AMBIGUOUS** + planner — `production_routing.py:120–134` |
| **DOC** | `INTENT_DOC` | **Yes** — **only** if `is_docs_artifact_intent` — lines 85–96 | **No** | `_docs_seed_plan` if shape `docs_seed_lane` — lines 212–236 | **Rules-first**, not model | If docs detector misses, model might emit **CODE_SEARCH/EXPLAIN** instead — different path |
| **EXPLAIN** | `INTENT_EXPLAIN`; legacy `CODE_EXPLAIN` | **Yes** — adapter | **Yes** | Single `EXPLAIN` — 279–305 | First-class | Low conf → AMBIGUOUS + planner |
| **EDIT** | `INTENT_EDIT`; legacy `CODE_EDIT` | **Yes** — adapter | **Yes** | **`plan()`** — line 334+ | First-class label, **not** a dedicated plan template | Always planner |
| **VALIDATE** | `INTENT_VALIDATE` in `intent.py`, `default_plan_shape` → `single_step_validate` | **No** — `_LEGACY_TO_INTENT` has **no** path to VALIDATE — `intent.py:147–153`; production never constructs VALIDATE | **No** | **`plan()`** — same bucket as EDIT | **Taxonomy-only / fake first-class** for production | N/A — never emitted |
| **INFRA** | `INTENT_INFRA`; legacy `INFRA` | **Yes** — adapter | **Yes** | Single `INFRA` — 306–332 | First-class | Low conf → AMBIGUOUS + planner |
| **COMPOUND** | `INTENT_COMPOUND` | **Yes** — **only** two-phase docs+code — `production_routing.py:98–109` | **No** | **Parent:** `_build_two_phase_parent_plan`; **flat `get_plan`:** planner + override — lines 180–183, 334+ | **Real** for one pattern; **otherwise unreachable** from production | Flat path **forces** planner; `secondary_intents` **ignored** by resolver |
| **AMBIGUOUS** | `INTENT_AMBIGUOUS`; legacy `GENERAL` | **Yes** — GENERAL mapping, model failure → GENERAL, invalid cat → GENERAL, **confidence fallback**, **router disabled** | **Yes** (GENERAL) | **`plan()`** | Mix of **user-ambiguous** and **system defer** | Router disabled uses same primary with `clarification_needed=False` — **overloaded** — `production_routing.py:73–83` |

**Blunt summary:** The **interface** is 8-way; the **production emitter** is **5 legacy labels + 2 deterministic predicates + synthetic disabled**. **VALIDATE is structurally absent** from production emission.

---

## 4. Truth-table audit (instruction → RoutedIntent → resolver branch)

Assumptions: `ENABLE_INSTRUCTION_ROUTER=True`, `ROUTER_TYPE` unset (inline model), confidence above threshold when relevant. **Model outputs are illustrative** — real model may differ.

| Example instruction | First path that fires | RoutedIntent (primary / shape) | `get_plan` branch | Override? | Comment |
|---------------------|------------------------|----------------------------------|-------------------|-----------|---------|
| “Where is `login()` defined?” | Legacy model → CODE_SEARCH | SEARCH / `single_step_search` | Single SEARCH | No | Model-dependent quality. |
| “Find the README for installation” | `is_docs_artifact_intent` | DOC / `docs_seed_lane` | `_docs_seed_plan` | No | **Docs detector wins** before model. |
| “Find architecture docs and explain replanner flow” | `is_two_phase_docs_code_intent` (and not docs-artifact) | COMPOUND / `two_phase_docs_code` | **`get_parent_plan`**: two-phase parent, **not** `get_plan` alone | No | **Order-dependent:** “explain” in `NON_DOCS_TOKENS` blocks pure docs-artifact — `docs_intent.py:69–70`. |
| “Explain how retries work” | Legacy → CODE_EXPLAIN | EXPLAIN / `single_step_explain` | Single EXPLAIN | No | |
| “Refactor `foo` for clarity” | Legacy → CODE_EDIT | EDIT / `planner_multi_step` | `plan()` | No | |
| “Run pytest on `tests/unit`” | Legacy → **CODE_EDIT or GENERAL** (typical) | EDIT or AMBIGUOUS / planner shape | `plan()` | No | **No VALIDATE** from router — `instruction_router.py:14`. |
| “Add a Dockerfile” | Legacy → INFRA or EDIT | INFRA or EDIT | INFRA single step **or** planner | No | Model may disagree; **no deterministic infra** string layer. |
| “Find X and also Y” (multi-goal) | Legacy single category | **One** of SEARCH/EDIT/EXPLAIN/INFRA/GENERAL | Short-circuit **or** planner | No | **No native COMPOUND** from model. |
| “fix this” | Legacy → GENERAL or EDIT | AMBIGUOUS or EDIT | `plan()` | No | |
| “” / whitespace | Model error or GENERAL | AMBIGUOUS | `plan()` | No | |
| Router disabled | N/A | AMBIGUOUS + `router_disabled` | `plan()` — lines 193–209 | No | **Not** “clarification” — `clarification_needed=False`. |

**Edge case — docs vs two-phase:**  
`is_docs_artifact_intent` requires **no** `NON_DOCS_TOKENS` including **`explain`** — `docs_intent.py:46–47, 69–70`. So **“find docs and explain …”** cannot be pure DOC; it can become **two-phase** if code markers match — `docs_intent.py:89–90`.

---

## 5. Split-brain residue audit

| Question | Finding |
|----------|---------|
| **Docs vs legacy still separate authorities?** | **Yes.** Docs path is **substring rules** in `docs_intent.py`. Legacy path is **LLM** in `instruction_router.py`. They are **sequenced**, not fused: docs rules **short-circuit** the model when they match — `production_routing.py:85–96`. |
| **`route_production_instruction` = arbitration or priority chain?** | **Priority chain.** First matching stage wins. No scoring, no reconciliation of conflicting signals beyond **docs before two-phase before model**. |
| **Duplicate token logic?** | **Yes, across non-production code.** `simple_router.py` uses **different** DOC/SEARCH marker sets than `docs_intent.py` — e.g. `simple_router` `_DOC_MARKERS` vs `DOCS_INTENT_TOKENS` + verbs. **Production does not call `simple_router`.** |
| **Routing outside RoutedIntent path?** | **Planner** still decides detailed steps for most user requests. **`_docs_seed_plan`** hardcodes query tweaks (`architecture`, `install`) — `plan_resolver.py:99–102` — **downstream** of RoutedIntent, not part of routing entrypoint. **`_derive_phase_subgoals`** splits text for two-phase — `plan_resolver.py:353+` — separate from `RoutedIntent.secondary_intents`. |

**Fixed vs looks fixed:**  
- **Fixed:** `plan_resolver` no longer embeds `_is_docs_artifact_intent` copy; single `route_production_instruction`.  
- **Looks fixed:** “Unified taxonomy” — **VALIDATE/COMPOUND (general)** still **not** producible from the model path.  
- **Still coupled:** **Eval router_registry** still speaks **5 labels** — `router_registry.py:11–16`.

---

## 6. Override and fallback audit

| Mechanism | Location | Trigger | Effect | Debt or product? |
|-----------|----------|---------|--------|------------------|
| **COMPOUND → planner on flat `get_plan`** | `plan_resolver.py:178–183, 334+` | `ri.primary_intent == COMPOUND` and router enabled | `plan()`, telemetry `routing_overridden_downstream` | **Honest debt** — flat resolver cannot run parent decomposition. |
| **`ignore_two_phase`** | `production_routing.py:99`; `get_plan` line 175 | Two-phase **build failed**; passed into `route_production_instruction` | Skips step 2; may re-classify via model | **Product fallback**; second classification pass possible. |
| **Router disabled** | `production_routing.py:73–83`; `get_plan` 193–209 | `ENABLE_INSTRUCTION_ROUTER` false | Always `plan()`; synthetic RoutedIntent | **Product** — but **overloads AMBIGUOUS** primary for telemetry. |
| **Low-confidence short-circuit** | `production_routing.py:115–134` | CODE_SEARCH/CODE_EXPLAIN/INFRA + low confidence | **AMBIGUOUS** + planner | **Product-aligned** with old GENERAL behavior. |
| **Two-phase parent failure** | `plan_resolver.py:604–633` | Exception in `_build_two_phase_parent_plan` | `get_plan(..., ignore_two_phase=True)` + compatibility parent | **Product fallback**. |
| **`suggested_plan_shape` ignored** | `plan_resolver.py` | Most shapes | Resolver uses **primary** for branching; DOC uses **shape** as gate | **Debt** — docstring in `intent.py:41–42` admits shapes are hints. |
| **`routed_intent_from_router_decision` default shape** | `intent.py:156–168` | Legacy mapping | Sets `suggested_plan_shape` via `default_plan_shape` | Harmless unless consumer trusts shape for EDIT/AMBIGUOUS. |

---

## 7. DOC and two-phase audit

- **Docs-artifact:** `is_docs_artifact_intent` — discovery verb + docs token + **no** `NON_DOCS_TOKENS` — `docs_intent.py:52–70`.  
- **Two-phase:** discovery + docs + **code markers** (`explain`, `flow`, `function `, …) — `73–91`. **Explicitly false** if docs-artifact — line 80–81.  
- **`_docs_seed_plan`:** Always **SEARCH_CANDIDATES → BUILD_CONTEXT → EXPLAIN** in **docs** artifact mode — `plan_resolver.py:95–131`. So **DOC RoutedIntent → multi-step docs pipeline**, not a single action.  
- **DOC vs EXPLAIN:** Pure docs-artifact ends **DOC**; the **plan** still includes an **EXPLAIN** **step** with `artifact_mode=docs` — that is **execution**, not `INTENT_EXPLAIN` for code.  
- **Is DOC first-class?** **Yes for routing entry**, **no for the legacy model** — the model **never** emits DOC. It is a **rules pre-filter**.  
- **Two-phase as COMPOUND?** **Yes** — `COMPOUND` + `two_phase_docs_code` — `production_routing.py:98–109`. **`secondary_intents=(DOC, EXPLAIN)`** is **not** read** by `_build_two_phase_parent_plan` — informational.  
- **Fragile/order-dependent:** **“explain”** in `NON_DOCS_TOKENS` prevents pure DOC classification when user says “find docs **and explain** …” — forces two-phase or model path instead of docs-only. **Concrete:** `"Find the README and explain what it says"` — if phrasing hits `explain` in non-docs check... actually `explain` is in NON_DOCS — **docs-artifact false**; could be two-phase if `architecture docs` + `explain` in string — **order of checks**: docs-artifact fails → two-phase evaluated.

---

## 8. VALIDATE audit

- **Production emit VALIDATE?** **No.** `route_production_instruction` never sets `primary_intent=VALIDATE`. `_LEGACY_TO_INTENT` has no VALIDATE key — `intent.py:147–153`.  
- **Downstream:** `get_plan` has **no** `if ri.primary_intent == INTENT_VALIDATE` — **334** sends VALIDATE to **`plan()`** like EDIT. **`PLAN_SHAPE_SINGLE_STEP_VALIDATE`** is **never** used by `plan_resolver` branching.  
- **Examples:**  
  - “run pytest” → model likely **CODE_EDIT** or **GENERAL** → EDIT or AMBIGUOUS → **planner**.  
  - “run tests for parser” → same.  
  - “verify the fix” → **EDIT**-ish or GENERAL.  
  - “check whether this passes” → GENERAL/EDIT.  
- **Verdict:** **VALIDATE is taxonomy + `simple_router` tests only** — **not** production-first-class. **`route_intent_simple` “VALIDATE”** does **not** connect to `get_plan` in production.

---

## 9. COMPOUND audit

- **Where produced:** `production_routing.py:98–109` **only** (two-phase). **`route_intent_simple`** can produce COMPOUND for multi-marker — **tests only**.  
- **`secondary_intents`:** Set to `(DOC, EXPLAIN)` in production — **telemetry**; `_build_two_phase_parent_plan` does **not** branch on them — it uses **instruction** string and `plan()` for phase 1 — `plan_resolver.py:514–536`.  
- **`decomposition_needed`:** **Not read** by orchestrator in production code paths surveyed.  
- **`two_phase_docs_code` only meaningful COMPOUND?** **In production, yes.** Any other COMPOUND would have to be **injected** via `routed_intent` (tests) or future code.  
- **Non-doc compound:** **Collapses to single legacy label** from model — **no COMPOUND** from LLM.  
- **Verdict:** **COMPOUND is real for one deterministic pattern** + **parent plan**; otherwise **placeholder** / **telemetry** for schema completeness.

---

## 10. AMBIGUOUS audit

**Cases that yield `INTENT_AMBIGUOUS` in production:**

1. `ENABLE_INSTRUCTION_ROUTER` false — synthetic, `clarification_needed=False` — `production_routing.py:73–83`.  
2. Legacy category **GENERAL** — `routed_intent_from_router_decision` — `intent.py:152–163`, `clarification_needed=True`.  
3. Unknown category → normalized to GENERAL in router — `instruction_router.py:104–106`.  
4. Model failure / invalid JSON → GENERAL — `instruction_router.py:87–88, 101–102`.  
5. **Confidence fallback** from CODE_SEARCH/CODE_EXPLAIN/INFRA — explicit AMBIGUOUS — `production_routing.py:120–134`, `clarification_needed=True`.  

**Overload:** Same primary **`AMBIGUOUS`** for **router disabled** vs **user-ambiguous** — distinguished only by **`matched_signals`** (`router_disabled` vs `legacy:GENERAL` / `confidence_below_threshold`).  

**`clarification_needed`:** Set **True** for GENERAL path and confidence fallback; **False** for router disabled — **intentional split** in code but **easy to mis-read** in dashboards if only `primary` is shown.  

**Consumed?** **Not** by `plan_resolver` for branching — only **telemetry** + `to_dict` in log events. **Surfaced** via logs/telemetry, **not** used to block `plan()`.

---

## 11. Legacy router gap audit

- **Emits:** `CODE_SEARCH`, `CODE_EDIT`, `CODE_EXPLAIN`, `INFRA`, `GENERAL` — `instruction_router.py:14`.  
- **Maps to RoutedIntent:** `_LEGACY_TO_INTENT` — `intent.py:147–153`; **GENERAL → AMBIGUOUS**.  
- **Cannot express:** **DOC, VALIDATE, COMPOUND** (multi-intent).  
- **Adapter compensates:** Docs/two-phase **preempt** model; confidence fallback **maps** failed short-circuit to AMBIGUOUS.  
- **Judgment — main blocker:** **Legacy 5-way router is the bottleneck** for **semantic coverage** (validate, multi-intent, native DOC). **Decomposition** is a **second** bottleneck — **not implemented** in production (COMPOUND only for fixed two-phase).  

**Which is worse?** For **current code**, expanding **router categories** (or prompt) fixes **label mismatch**; **decomposition** fixes **structure** but **nothing in `plan_resolver` reads `decomposition_needed`** — so integration work would follow.

---

## 12. Test coverage audit (code-only)

| Area | Covered? | How |
|------|----------|-----|
| `get_plan` DOC / SEARCH / COMPOUND override / router off | **Partially** | `test_plan_resolver_routing.py` — **hand-built `RoutedIntent`**, patches `ENABLE_INSTRUCTION_ROUTER` on **`plan_resolver`** module |
| `route_production_instruction` two-phase + docs | **Yes** | Same file — patches `production_routing.ENABLE_INSTRUCTION_ROUTER` |
| **End-to-end** `route_production_instruction` → `get_plan` without injection | **Weak** | Not asserted in tests reviewed — **two pieces tested in isolation** |
| **Low-confidence fallback** | **No** dedicated test in `test_plan_resolver_routing` | Would need mock `route_instruction` + threshold |
| **VALIDATE** in production path | **No** | `test_intent_routing` only uses **`route_intent_simple`** |
| **`test_two_phase_execution`** | **Detection** imports `is_two_phase_docs_code_intent` from **`docs_intent`** — exercises **predicate**, not full `get_parent_plan` routing stack in every test | Large file — **detection tests** covered; **unified router** not the focus |

**Fake confidence risk:** Tests that only build `RoutedIntent` and call `get_plan` **prove branch wiring**, not that **production classification** would ever produce that `RoutedIntent` (except where `route_production_instruction` is called).

---

## 13. Routing confusion matrix

| Pair | Cause in code | Example | Risk |
|------|---------------|---------|------|
| DOC vs SEARCH | Docs rules run **before** model; model never emits DOC | “Find README” → DOC + docs seed vs “find symbol” → SEARCH | **Medium** — different pipelines |
| DOC vs EXPLAIN | Docs plan **ends with EXPLAIN step** in docs mode — different from INTENT_EXPLAIN | “What’s in README?” | **Conceptual** — same action name, different lane |
| EXPLAIN vs EDIT | Legacy labels only | “Explain and fix” | **High** — **single** category from model |
| VALIDATE vs EDIT | No VALIDATE label | “run tests” | **High** — both → planner **or** EDIT guess |
| INFRA vs EDIT | Model only | Dockerfile change | **Medium** |
| COMPOUND vs single | Only **one** COMPOUND producer in production | “find docs + explain code” | **Low** for that pattern; **high** for arbitrary multi-goal |
| AMBIGUOUS vs forced label | GENERAL; disabled router | | **Telemetry** must use **signals** to distinguish |

---

## 14. General-software-assistant fitness

- **Over-specificity:** **Two-phase** pattern is **narrow** (specific substrings).  
- **Under-modeling:** **VALIDATE**, **general COMPOUND**.  
- **Planner over-reliance:** **Default** for EDIT, VALIDATE, AMBIGUOUS, COMPOUND-on-flat.  
- **Hidden priority:** **Docs → two-phase → model** — **not** visible in `RoutedIntent` alone.  
- **Extensibility:** Adding intents requires **touching** `production_routing`, **`_LEGACY_TO_INTENT`**, **`get_plan` branches**, **legacy prompt**.  
- **Observability:** **Good** — telemetry keys in `_merge_routing_telemetry`.  

**Verdict:** **Usable but transitional** — not **structurally complete** for taxonomy advertised in `intent.py`.

---

## 15. Decision-grade recommendation

**Choose:** **Expand legacy router categories and prompt (or replace small-model router) so native outputs include at least DOC, VALIDATE, and optionally COMPOUND — *or* formally remove VALIDATE/COMPOUND from “production” claims and keep them schema-only.**

**Defense:** Code proves **VALIDATE cannot be emitted** and **`PLAN_SHAPE_SINGLE_STEP_VALIDATE` is unused** in `plan_resolver`. Continuing to imply full 8-way parity is **documentation debt**, not just missing tests.

**Do NOT touch next (per your constraint):** Planner internals, patch generation, eval harness — **unless** you add router categories (prompt/registry only).

**Wait until later:** **True decomposition** for arbitrary COMPOUND — **`decomposition_needed` is not consumed**; building it without consumers is **placeholder work**.

**Success criteria for next stage:**  
1. **At least one** production path emits **`INTENT_VALIDATE`** **iff** you keep it in taxonomy.  
2. **`get_plan`** either branches on VALIDATE with a **defined** plan template **or** the contract drops `PLAN_SHAPE_SINGLE_STEP_VALIDATE`.  
3. **Legacy router** `ROUTER_CATEGORIES` / prompt / JSON schema updated in code — **verifiable by grep**.  
4. Tests: **mock `route_instruction`** returning new categories **and** assert **`get_plan`** branch — not only hand-built `RoutedIntent`.

---

## Top 10 routing risks

1. **VALIDATE is dead** in production emission.  
2. **Legacy single-label** for multi-intent user requests.  
3. **AMBIGUOUS** overload (disabled vs user ambiguity).  
4. **`suggested_plan_shape`** mostly **non-authoritative** — docstring admits it.  
5. **COMPOUND on flat `get_plan`** always planner — **surprising** if caller expected decomposition.  
6. **Docs vs model** disagreement **not reconciled** — first match wins.  
7. **`simple_router` diverges** from `docs_intent` — **test-only** but **misleading** if read as prod behavior.  
8. **router_eval registry** — still **5-way** — `router_registry.py`.  
9. **Double `_merge_routing_telemetry`** in parent + child path — **last write** semantics.  
10. **Confidence fallback** only for **three** categories — **CODE_EDIT** low conf still **EDIT** → planner (no AMBIGUOUS) — `production_routing.py:116–118` vs **EDIT not in** `_SHORT_CIRCUIT_ROUTER_CATEGORIES`.

## Top 10 routing strengths

1. **Single function** `route_production_instruction` for classification entry.  
2. **`plan_resolver` no longer duplicates** docs string logic.  
3. **Telemetry** fields for audit.  
4. **Explicit** two-phase **COMPOUND** + parent plan hook.  
5. **Confidence fallback** preserved from legacy short-circuit behavior.  
6. **`ignore_two_phase`** for **clean** fallback path.  
7. **`get_parent_plan` passes `routed_intent`** — avoids **double** model call in happy path.  
8. **`docs_intent`** isolated — **testable** predicates.  
9. **`RoutedIntent.to_dict`** for logging.  
10. **router_registry** optional — **pluggable** eval routers still map to same pipeline.

## Top 5 next actions (ordered)

1. **Decide:** Native **VALIDATE** (extend `RouterDecision` + prompt + `_LEGACY_TO_INTENT` + `get_plan` branch) **or** **remove VALIDATE** from production contract.  
2. **Update `instruction_router.py`** `ROUTER_CATEGORIES` and parsing if expanding — **single source of truth**.  
3. **Add integration tests:** `patch route_instruction` → `get_plan` / `get_parent_plan` **without** hand-built `RoutedIntent`.  
4. **Align `router_registry.py`** mapping for any new categories.  
5. **Either** wire **`decomposition_needed`** to a consumer **or** stop implying it drives behavior.

---

*End of audit.*
