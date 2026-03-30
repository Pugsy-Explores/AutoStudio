"""
Regression: exploration prompts built via PromptRegistry match legacy f-string output (byte-for-byte).

Legacy strings are duplicated from pre-migration inline prompts for fixed fixtures.
"""

import json
from pathlib import Path

import pytest

from agent.prompt_system.loader import load_from_versioned, normalize_model_name_for_path
from agent.prompt_system.registry import get_registry
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.llm_input_normalize import (
    format_explored_locations_for_prompt,
    normalize_analyzer,
    normalize_scoper,
    normalize_selector_batch,
    normalize_selector_single,
)


def _legacy_query_intent(instruction: str) -> str:
    return f"""ROLE:
You are a senior software engineer specializing in codebase exploration and retrieval.

TASK:
Convert the instruction into structured search queries that will be used to locate relevant code.

CONTEXT:
Instruction:
{instruction}

CONSTRAINTS:

Output MUST be retrieval-ready (not natural language summaries)
Prefer identifiers (class/function names) when possible
Break instruction into:
core concepts
likely symbols
searchable terms
Generate multiple query types for recall + precision balance
Do NOT invent unknown identifiers; infer only when strongly implied

OUTPUT FORMAT (STRICT JSON) — use either canonical or alias keys (both accepted):
{{
"symbols": ["string"],
"keywords": ["string"],
"regex_patterns": ["string"],
"intents": ["string"]
}}
Alias form (equivalent): symbol_queries, text_queries, single string "intent".

QUALITY BAR:

symbols / symbol_queries should target exact code entities when possible
keywords / text_queries should cover variations of the instruction
Avoid generic terms (e.g., "code", "function")

VERIFICATION:
Before returning, ensure:

queries are specific enough to retrieve code
at least one query targets a concrete symbol or concept
no redundant or duplicate queries

Return ONLY JSON.
"""


def _legacy_scoper(instruction: str, payload: list) -> str:
    candidates_json = normalize_scoper(instruction=instruction, rows=payload)
    return f"""ROLE:
You are a senior software engineer performing codebase exploration with strict precision.

TASK:
Select ONLY the candidate files that are causally necessary to answer the instruction.

CONTEXT:
Normalized candidates (includes instruction under [Global]):
{candidates_json}

CONSTRAINTS:

Select a subset of indices that are strictly necessary (not just related)
Necessary = file likely contains code that directly implements or explains the instruction
Prefer:
core implementation files over wrappers/tests
files containing primary logic (classes, functions, execution flow)
Avoid:
loosely related utilities
files matching only by keyword similarity
If unsure, prefer fewer candidates over more
NEVER select all candidates unless each is clearly necessary

Each candidate row includes an "index" field; use those values in selected_indices.
Do not invent files or indices. If none are necessary, return an empty list.

OUTPUT FORMAT (STRICT JSON):
{{
"selected_indices": [int]
}}

QUALITY BAR:
Selected set should minimize exploration steps downstream
Each selected file must have a clear reasoning path to solving the instruction

VERIFICATION:
Before returning, ensure:
Removing any selected file would reduce ability to answer the instruction
No selected file is included based only on keyword similarity

Return ONLY JSON.
"""


def _legacy_selector_single(instruction: str, payload: list) -> str:
    candidates_json = normalize_selector_single(items=payload)
    return (
        "You are selecting the most relevant code location.\n"
        "Return STRICT JSON only: {\"file_path\":\"...\",\"symbol\":\"...\"}.\n"
        "Prefer implementation files over tests and already explored files.\n\n"
        f"Instruction:\n{instruction}\n\nCandidates:\n{candidates_json}"
    )


def _legacy_selector_batch(
    instruction: str,
    explored_block: str,
    payload: list,
    limit: int,
) -> str:
    candidates_json = normalize_selector_batch(
        instruction=instruction,
        intent="no intent",
        limit=limit,
        explored_block=explored_block,
        items=payload,
    )
    return f"""ROLE:
You are a senior software engineer selecting the most causally necessary code locations.

TASK:
Select the minimal set of candidates required to answer the instruction.

CONTEXT:
Normalized batch (includes instruction, intent, limit, explored locations, and items):
{candidates_json}

CONSTRAINTS:

Necessary = directly contributes to solving the instruction (not just related)
Ensure coverage across different relevant components; avoid selecting multiple redundant candidates from the same narrow area unless necessary.
Prefer implementation logic over wrappers/tests
Select as few candidates as possible
If none are necessary, return no_relevant_candidate=true
Do NOT select based on keyword similarity alone

OUTPUT (STRICT JSON):
{{
"selected": [{{"file_path": "string", "symbol": "string"}}],
"selected_symbols": {{"0": ["OutlineSymbol"]}},
"no_relevant_candidate": boolean
}}
When outline_for_prompt is present on a candidate, you may add selected_symbols keyed by index string with 1–3 names from that outline.
If no candidate is necessary, use an empty selected array and set no_relevant_candidate to true.

VERIFICATION:

Each selected item must be required to answer the instruction
Removing any item should reduce correctness

Return ONLY JSON.
The closing brace of the JSON object must be the last character of the response (no text after it).
"""


def _legacy_analyzer(
    instruction: str,
    file_path: str,
    snippet: str,
    *,
    symbol_relationships_block: str = "(not provided)",
) -> str:
    exploration_llm_input = normalize_analyzer(
        instruction=instruction,
        intent="",
        task_intent_summary="(not provided)",
        file_path=file_path,
        snippet=snippet[:6000],
        symbol_relationships_block=symbol_relationships_block,
        context_blocks=[
            {
                "file_path": file_path,
                "start": 1,
                "end": 1,
                "content": snippet[:6000],
                "origin_reason": "",
                "symbol": None,
                "relationship_refs": [],
            }
        ],
    )
    return f"""ROLE:
You are a senior engineer determining whether this code is necessary to answer the instruction.

TASK:
Classify the snippet based on whether it directly contributes to solving the instruction.

CONTEXT:
Normalized exploration input (Global, Relationships, context blocks; lossless):
{exploration_llm_input}

CONSTRAINTS:

Each stated gap in your reasoning MUST be specific and actionable: name an exact symbol, file, or missing relationship or dependency; avoid vague phrases like "missing context" or "need more information".
If any required dependency, symbol, or flow is missing from the evidence, status MUST NOT be "sufficient"; prefer "partial" or "wrong_target" as appropriate.

Symbol relationships (when present) are supplementary; the snippet/code is the primary source of truth — use graph hints only if they help close gaps, not to override the code.

"sufficient" ONLY if the snippet alone is enough to satisfy the instruction.
Base decision on whether the snippet satisfies the instruction; use code logic when reasoning is required.
"partial" if related but incomplete.
"wrong_target" if not useful for solving the instruction.
"expand" means follow relations.
"refine" means search alternatives.
"stop" means enough info.

needs must use only: more_code, callers, callees, definition, different_symbol (as appropriate).
When status is wrong_target and the entire file is irrelevant, set wrong_target_scope to "file"; otherwise null.

OUTPUT (STRICT JSON):
{{
"status": "wrong_target|partial|sufficient",
"needs": ["more_code|callers|callees|definition|different_symbol"],
"reason": "string",
"next_action": "expand|refine|stop"
}}

VERIFICATION:
Would this snippet alone help solve the instruction? If not, it cannot be "sufficient".
Return ONLY JSON.
"""


@pytest.fixture
def reg():
    return get_registry()


def test_query_intent_uses_role_separated_prompt_parts(reg):
    instruction = "hello\nworld"
    prev = json.dumps({"symbols": ["Foo"], "keywords": ["bar"]})
    system_prompt, user_prompt = reg.render_prompt_parts(
        "exploration.query_intent_parser",
        model_name=None,
        variables={
            "instruction": instruction,
            "previous_queries": prev,
            "failure_reason": "no_results",
        },
    )
    assert "Output schema" in system_prompt
    assert instruction in user_prompt
    assert prev in user_prompt
    assert "no_results" in user_prompt


def test_prompt_parts_cache_smoke(reg):
    variables = {"instruction": "x", "previous_queries": "", "failure_reason": ""}
    a = reg.render_prompt_parts("exploration.query_intent_parser", variables=variables)
    b = reg.render_prompt_parts("exploration.query_intent_parser", variables=variables)
    assert a == b


def test_scoper_matches_legacy(reg):
    instruction = "inst"
    payload = [
        {"index": 0, "file_path": "a.py", "sources": ["sym"], "snippets": ["snip"], "symbols": [None]}
    ]
    cj = normalize_scoper(instruction=instruction, rows=payload)
    tmpl = reg.get_instructions("exploration.scoper")
    built = tmpl.format(candidates_json=cj) + "\n"
    assert built == _legacy_scoper(instruction, payload)


def test_selector_single_matches_legacy(reg):
    instruction = "do the thing"
    payload = [{"file_path": "f.py", "symbol": None, "source": "search"}]
    tmpl = reg.get_instructions("exploration.selector.single")
    built = tmpl.format(
        instruction=instruction,
        candidates_json=normalize_selector_single(items=payload),
    )
    assert built == _legacy_selector_single(instruction, payload)


def test_selector_batch_matches_legacy(reg):
    instruction = "ins"
    explored_block = format_explored_locations_for_prompt({("x", "")}, max_rows=100)
    pl = [{"file_path": "f", "symbol": None, "source": "search"}]
    limit = 2
    tmpl = reg.get_instructions("exploration.selector.batch")
    built = (
        tmpl.format(
            candidates_json=normalize_selector_batch(
                instruction=instruction,
                intent="no intent",
                limit=limit,
                explored_block=explored_block,
                items=pl,
            ),
        )
        + "\n"
    )
    assert built == _legacy_selector_batch(instruction, explored_block, pl, limit)


def test_analyzer_template_formats_with_all_placeholders(reg):
    """Qwen model path uses user_prompt_template with exploration_llm_input."""
    instruction = "instr"
    fp = "p/q/r.py"
    snippet = ("code " * 400)[:6000]
    _sys, user_t = reg.render_prompt_parts(
        "exploration.analyzer",
        model_name="qwen2.5-coder-7b",
    )
    exploration_llm_input = normalize_analyzer(
        instruction=instruction,
        intent="retrieval_kw",
        task_intent_summary="type=explanation; scope=narrow",
        file_path=fp,
        snippet=snippet,
        symbol_relationships_block="(not provided)",
        context_blocks=[
            {
                "file_path": fp,
                "start": 1,
                "end": 2,
                "content": snippet,
                "origin_reason": "",
                "symbol": None,
                "relationship_refs": [],
            }
        ],
    )
    user_rendered = user_t.format(exploration_llm_input=exploration_llm_input)
    assert instruction in user_rendered
    assert "retrieval_kw" in user_rendered
    assert "type=explanation" in user_rendered
    assert "{instruction}" not in user_rendered

    default_tmpl = reg.get_instructions("exploration.analyzer")
    legacy = (
        default_tmpl.format(
            exploration_llm_input=normalize_analyzer(
                instruction=instruction,
                intent="",
                task_intent_summary="(not provided)",
                file_path=fp,
                snippet=snippet,
                symbol_relationships_block="(not provided)",
                context_blocks=[
                    {
                        "file_path": fp,
                        "start": 1,
                        "end": 1,
                        "content": snippet,
                        "origin_reason": "",
                        "symbol": None,
                        "relationship_refs": [],
                    }
                ],
            ),
        )
        + "\n"
    )
    assert legacy == _legacy_analyzer(instruction, fp, snippet, symbol_relationships_block="(not provided)")
    assert fp in legacy
    assert instruction in legacy


def test_model_specific_path_falls_back_to_default(reg):
    """Unknown model_name resolves to same v1.yaml as default."""
    t_default = reg.get_instructions("exploration.query_intent_parser", model_name=None)
    t_unknown = reg.get_instructions(
        "exploration.query_intent_parser",
        model_name="__nonexistent_model_slug_for_test__",
    )
    assert t_default == t_unknown


def test_normalize_model_name_for_path():
    assert normalize_model_name_for_path(None) is None
    assert normalize_model_name_for_path("  ") is None
    assert normalize_model_name_for_path("Qwen 9B") == "qwen_9b"
    assert normalize_model_name_for_path("gpt-4-turbo") == "gpt-4-turbo"


def test_get_model_for_task_aligns_with_task_models():
    """Single source: models_config task_models + same fallback as call_reasoning_model."""
    from agent.models.model_config import TASK_MODELS, get_model_for_task
    from agent_v2.exploration.exploration_task_names import (
        EXPLORATION_LLM_STAGE_TASKS,
        EXPLORATION_TASK_V2,
    )

    assert get_model_for_task(EXPLORATION_TASK_V2) == (TASK_MODELS.get(EXPLORATION_TASK_V2) or "REASONING")
    for task_name in EXPLORATION_LLM_STAGE_TASKS:
        assert get_model_for_task(task_name) == (TASK_MODELS.get(task_name) or "REASONING")
    assert get_model_for_task("") == "REASONING"
    assert get_model_for_task("planner") == (TASK_MODELS.get("planner") or "REASONING")


def test_load_from_versioned_model_subdir_smoke():
    """models/{norm}/v1.yaml resolution does not break when missing."""
    assert load_from_versioned("exploration.query_intent_parser", "v1", model_name="nope_nope_nope") is not None


def test_models_config_has_explicit_exploration_stage_model_and_params():
    """Guardrail: every exploration LLM stage must have task_models + task_params entries."""
    from agent_v2.exploration.exploration_task_names import (
        EXPLORATION_LLM_STAGE_TASKS,
        EXPLORATION_TASK_V2,
    )

    cfg_path = Path(__file__).resolve().parents[1] / "agent" / "models" / "models_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    task_models = cfg.get("task_models", {})
    task_params = cfg.get("task_params", {})

    required = (EXPLORATION_TASK_V2, *EXPLORATION_LLM_STAGE_TASKS)
    for task_name in required:
        assert task_name in task_models
        assert task_name in task_params


def test_option_b_model_specific_prompt_variants_exist_for_exploration_tasks():
    """
    Option B guardrail:
    prompt versions are keyed by normalized display model name from models_config.json.
    """
    from agent_v2.exploration.exploration_task_names import (
        EXPLORATION_TASK_ANALYZER,
        EXPLORATION_TASK_QUERY_INTENT,
        EXPLORATION_TASK_SCOPER,
        EXPLORATION_TASK_SELECTOR_BATCH,
        EXPLORATION_TASK_SELECTOR_SINGLE,
    )

    mapping = {
        EXPLORATION_TASK_QUERY_INTENT: "exploration.query_intent_parser",
        EXPLORATION_TASK_SCOPER: "exploration.scoper",
        EXPLORATION_TASK_SELECTOR_SINGLE: "exploration.selector.single",
        EXPLORATION_TASK_SELECTOR_BATCH: "exploration.selector.batch",
        EXPLORATION_TASK_ANALYZER: "exploration.analyzer",
    }

    root = Path(__file__).resolve().parents[1] / "agent" / "prompt_versions"
    missing: list[str] = []
    for task_name, prompt_name in mapping.items():
        model_name = get_prompt_model_name_for_task(task_name)
        norm = normalize_model_name_for_path(model_name)
        assert norm, f"normalized model name is empty for task={task_name} model={model_name!r}"
        path = root / prompt_name / "models" / norm / "v1.yaml"
        if not path.exists():
            missing.append(str(path))
        # Also verify loader resolves with this model_name.
        assert load_from_versioned(prompt_name, "v1", model_name=model_name) is not None
    assert not missing, f"missing Option B model prompt variant files: {missing}"
