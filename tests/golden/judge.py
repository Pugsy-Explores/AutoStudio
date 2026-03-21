"""
LLM-as-Judge for golden tests — optional semantic validation.

Phase 3: Add meaning layer without breaking determinism or generality.
- LLM judge is optional (ENABLE_LLM_JUDGE=False by default).
- Must not replace structural assertions.
- Must not hardcode domain logic.
- Must not override structural correctness.

Production-grade: dual-run stability, confidence label, caching, grounding signals.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Determinism guard: only enable when explicitly requested.
# Set ENABLE_LLM_JUDGE=1 to run semantic validation.
ENABLE_LLM_JUDGE = os.environ.get("ENABLE_LLM_JUDGE", "").strip().lower() in ("1", "true", "yes")

# File-based cache. Fallback to in-memory if file missing.
CACHE_PATH = Path("artifacts/judge_cache.json")
_JUDGE_CACHE: Dict[str, Dict[str, Any]] = {}


def _load_judge_cache() -> None:
    """Load cache from disk. Idempotent."""
    global _JUDGE_CACHE
    if not CACHE_PATH.exists():
        return
    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            _JUDGE_CACHE = data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not load judge cache from %s: %s", CACHE_PATH, e)


def _save_judge_cache() -> None:
    """Write cache to disk."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(_JUDGE_CACHE, f, indent=0)
    except OSError as e:
        logger.warning("Could not save judge cache to %s: %s", CACHE_PATH, e)


_load_judge_cache()


def aggregate_judgments(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generalize judge consensus from N runs. Returns passed, score, disagreement."""
    if not results:
        return {"passed": False, "score": 0, "disagreement": False}
    passes = [r.get("passed", False) for r in results]
    scores = [r.get("score", 0) for r in results]
    passed = all(passes)
    avg_score = sum(scores) // len(scores) if scores else 0
    disagreement = len(set(passes)) > 1
    return {"passed": passed, "score": avg_score, "disagreement": disagreement}


def _score_to_confidence(score: int) -> str:
    """Map score (0-100) to confidence label. high >= 80, medium >= 40, low < 40."""
    if score >= 80:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _build_judge_prompt(
    instruction: str,
    answer: str,
    context: str,
    criteria: str | None,
    answer_supported: bool | None = None,
    support_strength: int | float | None = None,
) -> str:
    """Build a general prompt. No domain assumptions."""
    parts = [
        "You are evaluating whether a system's output adequately responds to an instruction.",
        "",
        "## Instruction",
        instruction or "(none)",
        "",
        "## System output (answer)",
        answer or "(empty)",
        "",
    ]
    if answer_supported is not None or support_strength is not None:
        sig_lines = []
        if answer_supported is not None:
            sig_lines.append(f"- answer_supported: {answer_supported}")
        if support_strength is not None:
            sig_lines.append(f"- support_strength: {support_strength}")
        if sig_lines:
            parts.extend([
                "## System signal (context only; do not bias your decision)",
                *sig_lines,
                "",
            ])
    if context and context.strip():
        parts.extend([
            "## Provided context (optional)",
            context[:8000] + ("..." if len(context) > 8000 else ""),
            "",
        ])
    if criteria and criteria.strip():
        parts.extend([
            "## Additional criteria",
            criteria,
            "",
        ])
    parts.extend([
        "## Your task",
        "Evaluate:",
        "1. Does the answer address the instruction?",
        "2. Is the answer supported by the provided context (or is context not applicable)?",
        "3. Is there evidence of hallucination (claims not supported by context or instruction)?",
        "",
        "Respond with a single JSON object, nothing else:",
        '{"passed": true|false, "score": 0-100, "reason": "brief explanation"}',
    ])
    return "\n".join(parts)


def _parse_judge_response(raw: str) -> Dict[str, Any]:
    """Parse strict JSON from model response. Tolerate markdown code blocks."""
    text = raw.strip()
    # Try to extract JSON from markdown code block if present
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        text = m.group(1)
    else:
        # Look for first { ... } span
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i, c in enumerate(text[start:], start):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        text = text[start : i + 1]
                        break
    try:
        parsed = json.loads(text)
        return {
            "passed": bool(parsed.get("passed", False)),
            "score": int(parsed.get("score", 0)) if isinstance(parsed.get("score"), (int, float)) else 0,
            "reason": str(parsed.get("reason", ""))[:500],
        }
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("LLM judge response parse failed: %s", e)
        return {"passed": False, "score": 0, "reason": f"parse_error: {e!s}"[:500]}


def _run_once(
    instruction: str,
    answer: str,
    context: str,
    criteria: str | None,
    answer_supported: bool | None,
    support_strength: int | float | None,
) -> Dict[str, Any]:
    """Single judge run. Returns {passed, score, reason}."""
    from agent.models.model_client import call_reasoning_model

    prompt = _build_judge_prompt(
        instruction, answer, context, criteria, answer_supported, support_strength
    )
    sys_prompt = (
        "You are a strict evaluator. Respond only with valid JSON. "
        "No markdown, no extra text."
    )
    response = call_reasoning_model(
        prompt,
        system_prompt=sys_prompt,
        task_name="planner",
        max_tokens=256,
    )
    return _parse_judge_response(response or "")


def run_llm_judge(test: Dict[str, Any], result: Dict[str, Any], judge_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run optional LLM-based semantic validation.

    Dual-run stability: run twice, require both to pass. Track disagreement.
    Caching: same (instruction, answer) reuses cached result.
    Confidence: score -> high/medium/low.

    Args:
        test: Golden test dict (has input.instruction, etc.).
        result: Evaluation view (structure, metrics, signals/raw).
        judge_config: Config from test.llm_judge (enabled, criteria, etc.).

    Returns:
        {"passed": bool, "score": int, "reason": str, "confidence": str, "disagreement": bool}
    """
    if not ENABLE_LLM_JUDGE:
        return {"passed": True, "score": 0, "reason": "disabled", "confidence": "low", "disagreement": False}

    instruction = (test.get("input") or {}).get("instruction") or ""
    raw = result.get("signals") or result
    answer = raw.get("answer", "")
    if isinstance(answer, dict):
        answer = json.dumps(answer)[:4000]
    else:
        answer = str(answer or "").strip()

    context = raw.get("context", "")
    if isinstance(context, dict):
        context = json.dumps(context)[:4000]
    else:
        context = str(context or "").strip()

    criteria = (judge_config or {}).get("criteria", "")

    # Grounding signals (light; do not bias)
    metrics = raw.get("metrics") or result.get("metrics") or {}
    answer_supported = metrics.get("answer_supported")
    support_strength = metrics.get("support_strength") or metrics.get("average_support_strength")

    if not answer:
        return {
            "passed": False,
            "score": 0,
            "reason": "no answer provided for evaluation",
            "confidence": "low",
            "disagreement": False,
        }

    cache_key = hashlib.sha256((instruction + answer).encode()).hexdigest()
    if cache_key in _JUDGE_CACHE:
        return _JUDGE_CACHE[cache_key]

    try:
        runs = [
            _run_once(instruction, answer, context, criteria, answer_supported, support_strength),
            _run_once(instruction, answer, context, criteria, answer_supported, support_strength),
        ]
        agg = aggregate_judgments(runs)
        reason = "judge_disagreement" if agg["disagreement"] else runs[0]["reason"]
        confidence = _score_to_confidence(agg["score"])

        out = {
            "passed": agg["passed"],
            "score": agg["score"],
            "reason": reason,
            "confidence": confidence,
            "disagreement": agg["disagreement"],
        }
        _JUDGE_CACHE[cache_key] = out
        _save_judge_cache()
        return out
    except Exception as e:
        logger.warning("LLM judge call failed: %s", e)
        out = {
            "passed": False,
            "score": 0,
            "reason": f"judge_error: {e!s}"[:500],
            "confidence": "low",
            "disagreement": False,
        }
        return out
