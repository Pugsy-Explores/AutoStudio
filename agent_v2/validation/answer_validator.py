"""
Post-synthesis answer validation: deterministic rules plus optional LLM merge.

See Docs/architecture_freeze/planner_decision_synthesis_validation_audit.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_prompt_model_name_for_task
from agent.prompt_system.registry import get_registry
from agent_v2.config import get_config
from agent_v2.schemas.answer_synthesis import AnswerSynthesisResult
from agent_v2.schemas.answer_validation import AnswerValidationResult, ValidationConfidence
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.utils.json_extractor import JSONExtractor

_LOG = logging.getLogger(__name__)

ANSWER_VALIDATION_TASK = "ANSWER_VALIDATION"
PROMPT_REGISTRY_KEY = "answer_validation"


def _fmt_safe(s: str) -> str:
    return (s or "").replace("{", "{{").replace("}", "}}")


def _norm_gaps(exploration: FinalExplorationSchema) -> list[str]:
    raw = exploration.exploration_summary.knowledge_gaps or []
    return [str(g).strip() for g in raw if str(g).strip()]


def _derive_rules_validation_reason(
    *,
    is_complete: bool,
    issues: list[str],
    missing_context: list[str],
) -> str:
    """Short prose trace from structured fields (rules path and LLM fallback)."""
    if is_complete:
        return (
            "Deterministic validation passed: synthesis succeeded with sufficient signals "
            "and no blocking rule violations."
        )
    issue_slugs = {str(x).strip() for x in issues}
    mc = [str(x).strip() for x in missing_context if str(x).strip()][:8]
    human_mc = [x for x in mc if x not in issue_slugs and len(x) < 200]
    tags: list[str] = []
    for i in issues:
        s = str(i).strip()
        if "synthesis_not_successful" in s:
            tags.append("synthesis did not succeed")
        elif s == "empty_direct_answer":
            tags.append("missing direct answer")
        elif s == "uncertainty_stated":
            tags.append("stated uncertainty in the answer")
        elif s == "exploration_confidence_low":
            tags.append("low exploration confidence")
        elif s == "exploration_knowledge_gaps_present":
            tags.append("open knowledge gaps from exploration")
        elif s == "synthesis_coverage_weak":
            tags.append("weak synthesis coverage")
        elif s:
            tags.append(s[:120])
    seen: set[str] = set()
    parts: list[str] = []
    for p in human_mc + tags:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            parts.append(p)
        if len(parts) >= 6:
            break
    if not parts:
        return "Validation incomplete; see structured issues and missing_context."
    if len(parts) == 1:
        out = parts[0]
        return out[0].upper() + out[1:] + ("." if not out.endswith(".") else "")
    *rest, last = parts
    lead = ", ".join(rest)
    sentence = f"{lead} and {last}"
    return sentence[0].upper() + sentence[1:] + ("." if not sentence.endswith(".") else "")


def _outcome_confidence(
    is_complete: bool,
    exploration: FinalExplorationSchema,
    synthesis: AnswerSynthesisResult,
) -> ValidationConfidence:
    ex = (exploration.confidence or "").strip().lower()
    if not is_complete:
        if ex == "low" or synthesis.coverage == "weak":
            return "low"
        return "medium"
    if ex == "high" and synthesis.coverage == "sufficient":
        return "high"
    if ex in {"high", "medium"}:
        return "medium"
    return "low"


def _validate_answer_rules_only(
    *,
    exploration: FinalExplorationSchema,
    synthesis: AnswerSynthesisResult,
) -> AnswerValidationResult:
    issues: list[str] = []
    gaps = _norm_gaps(exploration)
    ex_conf = (exploration.confidence or "").strip().lower()

    if not synthesis.synthesis_success:
        msg = (synthesis.error or "synthesis_failed").strip() or "synthesis_failed"
        issues.append(f"synthesis_not_successful: {msg[:200]}")

    if not (synthesis.direct_answer or "").strip():
        issues.append("empty_direct_answer")

    unc = (synthesis.uncertainty or "").strip()
    if unc:
        issues.append("uncertainty_stated")

    if ex_conf == "low":
        issues.append("exploration_confidence_low")

    if gaps:
        issues.append("exploration_knowledge_gaps_present")

    if synthesis.coverage == "weak":
        issues.append("synthesis_coverage_weak")

    is_complete = len(issues) == 0
    missing_context: list[str] = list(gaps)
    for i in issues:
        if i.startswith("exploration_knowledge_gaps_present"):
            continue
        if i not in missing_context:
            missing_context.append(i)

    if not is_complete and not missing_context:
        missing_context.append("retrieve_more_evidence_for_instruction")

    conf = _outcome_confidence(is_complete, exploration, synthesis)
    reason = _derive_rules_validation_reason(
        is_complete=is_complete, issues=issues, missing_context=missing_context
    )
    return AnswerValidationResult(
        is_complete=is_complete,
        issues=issues,
        missing_context=missing_context,
        confidence=conf,
        validation_reason=reason[:800],
    )


def _min_confidence(a: ValidationConfidence, b: ValidationConfidence) -> ValidationConfidence:
    order = {"low": 0, "medium": 1, "high": 2}
    return a if order[a] <= order[b] else b


def _norm_str_list(raw: object, cap: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw[:cap]:
        s = str(x).strip()[:500]
        if s:
            out.append(s)
    return out


def _parse_validation_json(raw: str) -> AnswerValidationResult | None:
    parsed = JSONExtractor.extract_final_json(raw)
    if not isinstance(parsed, dict):
        return None
    ic = parsed.get("is_complete")
    if not isinstance(ic, bool):
        return None
    issues = _norm_str_list(parsed.get("issues"), 24)
    missing = _norm_str_list(parsed.get("missing_context"), 24)
    conf_raw = str(parsed.get("confidence") or "low").strip().lower()
    if conf_raw not in ("low", "medium", "high"):
        conf_raw = "low"
    vr = str(parsed.get("validation_reason") or "").strip()[:800]
    return AnswerValidationResult(
        is_complete=ic,
        issues=issues,
        missing_context=missing,
        confidence=cast(ValidationConfidence, conf_raw),
        validation_reason=vr,
    )


def _merge_validation_llm(
    base: AnswerValidationResult, llm: AnswerValidationResult
) -> AnswerValidationResult:
    is_complete = base.is_complete and llm.is_complete
    seen: set[str] = set()
    issues: list[str] = []
    for x in base.issues + llm.issues:
        if x not in seen:
            seen.add(x)
            issues.append(x)
        if len(issues) >= 32:
            break
    seen_m: set[str] = set()
    missing: list[str] = []
    for x in base.missing_context + llm.missing_context:
        if x not in seen_m:
            seen_m.add(x)
            missing.append(x)
        if len(missing) >= 32:
            break
    conf = _min_confidence(base.confidence, llm.confidence)
    derived = _derive_rules_validation_reason(
        is_complete=is_complete, issues=issues, missing_context=missing
    )
    lr = (llm.validation_reason or "").strip()[:800]
    reason = lr if lr else derived
    return AnswerValidationResult(
        is_complete=is_complete,
        issues=issues,
        missing_context=missing,
        confidence=conf,
        validation_reason=reason[:800],
    )


def _exploration_summary_short(exploration: FinalExplorationSchema) -> str:
    es = exploration.exploration_summary
    overall = (es.overall or "").strip()[:4000]
    kf = es.key_findings or []
    kf_lines = [str(x).strip()[:400] for x in kf if str(x).strip()][:8]
    parts = [f"Overall: {overall}"] if overall else []
    if kf_lines:
        parts.append("Key findings:\n" + "\n".join(f"- {x}" for x in kf_lines))
    return _fmt_safe("\n\n".join(parts) if parts else "(none)")


def _build_validation_prompt_variables(
    *,
    instruction: str,
    base: AnswerValidationResult,
    exploration: FinalExplorationSchema,
    synthesis: AnswerSynthesisResult,
) -> dict[str, str]:
    rules_json = json.dumps(base.model_dump(mode="json"), ensure_ascii=False, indent=2)
    expl = (exploration.confidence or "").strip() or "unknown"
    return {
        "instruction": _fmt_safe(instruction.strip()[:8000]),
        "rules_validation_json": _fmt_safe(rules_json[:12000]),
        "exploration_summary": _exploration_summary_short(exploration),
        "exploration_confidence": _fmt_safe(expl),
        "synthesis_direct_answer": _fmt_safe((synthesis.direct_answer or "").strip()[:8000]),
        "synthesis_structured_explanation": _fmt_safe(
            (synthesis.structured_explanation or "").strip()[:4000]
        ),
        "synthesis_coverage": _fmt_safe(str(synthesis.coverage or "unknown")),
        "synthesis_uncertainty": _fmt_safe((synthesis.uncertainty or "").strip()[:2000] or "(none)"),
    }


def _llm_validate_merge(
    base: AnswerValidationResult,
    *,
    instruction: str,
    exploration: FinalExplorationSchema,
    synthesis: AnswerSynthesisResult,
    langfuse_parent: Any | None,
) -> AnswerValidationResult:
    _ = langfuse_parent
    variables = _build_validation_prompt_variables(
        instruction=instruction, base=base, exploration=exploration, synthesis=synthesis
    )
    model_display = get_prompt_model_name_for_task(ANSWER_VALIDATION_TASK)
    system_prompt, user_prompt = get_registry().render_prompt_parts(
        PROMPT_REGISTRY_KEY,
        version="latest",
        variables=variables,
        model_name=model_display,
    )
    if not user_prompt.strip():
        return base
    raw = call_reasoning_model(
        user_prompt,
        system_prompt=system_prompt if system_prompt.strip() else None,
        task_name=ANSWER_VALIDATION_TASK,
        prompt_name=None,
    )
    parsed = _parse_validation_json(raw)
    if parsed is None:
        _LOG.warning("answer_validation_llm parse_failed raw_chars=%s", len(raw or ""))
        return base
    return _merge_validation_llm(base, parsed)


def validate_answer(
    *,
    instruction: str,
    exploration: FinalExplorationSchema,
    synthesis: AnswerSynthesisResult,
    langfuse_parent: Any | None = None,
) -> AnswerValidationResult:
    """
    Rules-first validation; optional LLM second pass when
    ``planner_loop.enable_answer_validation_llm`` is true.
    """
    base = _validate_answer_rules_only(exploration=exploration, synthesis=synthesis)
    loop = get_config().planner_loop
    if not loop.enable_answer_validation or not loop.enable_answer_validation_llm:
        return base
    try:
        return _llm_validate_merge(
            base,
            instruction=instruction,
            exploration=exploration,
            synthesis=synthesis,
            langfuse_parent=langfuse_parent,
        )
    except Exception as ex:
        _LOG.warning("answer_validation_llm failed: %s", ex, exc_info=True)
        return base
