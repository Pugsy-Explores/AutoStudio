"""
Global Tier 1–4 definitions and per-module interpretations for AutoStudio.

These are specification constants for dataset authoring and reporting — they do not
change runtime behavior. Pair with JSON tasks under eval/datasets/.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TierId = Literal[1, 2, 3, 4]

ModuleName = Literal[
    "planner",
    "decision",
    "exploration",
    "synthesizer",
    "validator",
]


@dataclass(frozen=True)
class GlobalTierSpec:
    """Cross-cutting difficulty rubric (applies to any module)."""

    tier: TierId
    summary: str
    scope: str
    dependencies: str
    ambiguity: str
    reasoning_mode: str


GLOBAL_TIERS: dict[TierId, GlobalTierSpec] = {
    1: GlobalTierSpec(
        tier=1,
        summary="Local / atomic",
        scope="Single function or file; no cross-cutting dependencies.",
        dependencies="None or trivially inlined.",
        ambiguity="None; specification is unambiguous.",
        reasoning_mode="Direct retrieval or single-step reasoning.",
    ),
    2: GlobalTierSpec(
        tier=2,
        summary="Multi-step (shallow)",
        scope="Few related functions or files.",
        dependencies="Simple chain (1–2 hops); no conflicting signals.",
        ambiguity="Low; minor disambiguation may be needed.",
        reasoning_mode="Short multi-step plan or shallow synthesis.",
    ),
    3: GlobalTierSpec(
        tier=3,
        summary="Cross-component / deep reasoning",
        scope="Multiple files, classes, or subsystems.",
        dependencies="Graph traversal 2–4 hops; interfaces and indirect callers.",
        ambiguity="Possible; information may be partial or spread across sources.",
        reasoning_mode="Synthesis across retrieved evidence; tradeoffs and ordering.",
    ),
    4: GlobalTierSpec(
        tier=4,
        summary="Debugging / failure / open-ended",
        scope="Noisy, incomplete, or adversarial context.",
        dependencies="Unknown or misleading; root cause not surface-level.",
        ambiguity="High; distractors and conflicting summaries possible.",
        reasoning_mode="Hypothesis → check → refine; may require replan or extra exploration.",
    ),
}


@dataclass(frozen=True)
class ModuleTierSpec:
    """What this tier means for a specific pipeline module."""

    tier: TierId
    planner: str
    decision: str
    exploration: str
    synthesizer: str
    validator: str


# Rows: tier. Columns: human-readable behavior for each module at that tier.
MODULE_TIER_DEFINITIONS: dict[TierId, ModuleTierSpec] = {
    1: ModuleTierSpec(
        tier=1,
        planner="Decompose into 1–2 steps with obvious ordering (e.g. SEARCH then EXPLAIN).",
        decision="Tool choice is obvious from instruction + thin context (e.g. explore vs act).",
        exploration="Direct retrieval: query maps cleanly to repo map / regex / vector hit.",
        synthesizer="Answer is largely extractive from a single dominant context block.",
        validator="Completeness is obvious: required fields present; no subtle reasoning gaps.",
    ),
    2: ModuleTierSpec(
        tier=2,
        planner="Plan spans a small set of steps across 2–3 files; dependencies are linear.",
        decision="Choose between a small closed set of actions; cues are consistent.",
        exploration="Shallow pipeline: intent → scope → select with 1–2 refinement loops max.",
        synthesizer="Combine two evidence snippets; light paraphrase beyond copy-paste.",
        validator="Catch missing bullets or weak coverage against a short explicit checklist.",
    ),
    3: ModuleTierSpec(
        tier=3,
        planner="Ordering and dependencies matter; may include parallelizable branches or replan stubs.",
        decision="Weigh exploration depth vs synthesis vs replan using conflicting soft signals.",
        exploration="Cross-file graph expansion (imports, callers) with ranking and pruning.",
        synthesizer="Multi-source reasoning: reconcile definitions, behaviors, and edge cases.",
        validator="Detect missing context, coverage gaps, or unstated assumptions from exploration.",
    ),
    4: ModuleTierSpec(
        tier=4,
        planner="Large or conditional decomposition; explicit retries, failure recovery, or phased rollout.",
        decision="Ambiguous: explore vs synthesize vs replan under partial observability.",
        exploration="Multi-hop expansion with noise; must prune misleading high-similarity nodes.",
        synthesizer="Abstract across partial evidence; flag uncertainty; avoid false precision.",
        validator="Find flawed reasoning, false confidence, or alternative hypotheses; drive loop exit.",
    ),
}
