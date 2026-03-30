"""
Tool policy for planner decision JSON — which act tools are allowed and shell constraints.

Enforced in PlannerV2 after parse + tool normalization, before and after explore-cap override,
then pairing validation. Executor and PlanValidator schemas stay unchanged; this is planning-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Final, Literal, Optional

from agent_v2.schemas.plan import PlannerEngineOutput, PlannerEngineStepSpec
from agent_v2.validation.plan_validator import PlanValidationError


class ToolPolicyViolationError(PlanValidationError):
    """
    Non-repairable tool policy violation (planner must not LLM-repair).

    Attributes are for telemetry; message remains human-readable.
    """

    def __init__(
        self,
        message: str,
        *,
        policy_tool: str = "",
        policy_reason: str = "",
        policy_command: str = "",
    ) -> None:
        super().__init__(message)
        self.policy_tool = policy_tool
        self.policy_reason = policy_reason
        self.policy_command = policy_command


# Shell metacharacters / command chaining — blocked in plan-mode shell (first-token path).
SHELL_FORBIDDEN_SUBSTRINGS: Final[tuple[str, ...]] = ("&&", ";", "|", "`")


@dataclass(frozen=True)
class ToolPolicy:
    """
    Static policy for one planner mode.

    - allowed_act_tools: engine.tool values permitted when decision == act
    - shell_first_token_allowlist: if set, run_shell commands must start with one of these
      tokens (basename of argv[0]); if None, shell first token is not policy-checked here
    """

    mode: Literal["plan", "act"]
    allowed_act_tools: frozenset[str]
    shell_first_token_allowlist: Optional[frozenset[str]]


# --- Locked plan-mode policy (staff review) ---------------------------------

PLAN_MODE_ALLOWED_SHELL_FIRST: Final[frozenset[str]] = frozenset({"ls", "rg", "grep", "cat"})

PLAN_MODE_TOOL_POLICY: Final[ToolPolicy] = ToolPolicy(
    mode="plan",
    allowed_act_tools=frozenset({"search_code", "open_file", "run_tests", "run_shell"}),
    shell_first_token_allowlist=PLAN_MODE_ALLOWED_SHELL_FIRST,
)

# All phase-1 act tools including edit; no shell token restriction (still subject to
# PlannerV2._validate_act_tool_inputs for non-empty command).
ACT_MODE_TOOL_POLICY: Final[ToolPolicy] = ToolPolicy(
    mode="act",
    allowed_act_tools=frozenset({"search_code", "open_file", "run_shell", "edit", "run_tests"}),
    shell_first_token_allowlist=None,
)


def shell_command_from_step(spec: PlannerEngineStepSpec) -> str:
    """Primary shell string from engine step (matches planner synthesis inputs)."""
    inp = (spec.input or "").strip()
    if inp:
        return inp
    md = spec.metadata or {}
    c = md.get("command")
    return str(c).strip() if c is not None else ""


def first_shell_argv0_token(cmd: str) -> str:
    """
    First whitespace-delimited token, de-quoted, basename only (e.g. /usr/bin/grep -> grep).
    """
    s = (cmd or "").strip()
    if not s:
        return ""
    first = s.split()[0]
    if len(first) >= 2 and first[0] == first[-1] and first[0] in ("'", '"'):
        first = first[1:-1]
    return PurePath(first).name


def shell_first_token_allowed(cmd: str, allowlist: frozenset[str]) -> bool:
    tok = first_shell_argv0_token(cmd)
    if not tok:
        return False
    return tok in allowlist


def shell_command_has_forbidden_substrings(cmd: str) -> bool:
    """True if command appears to chain or embed subshells (minimal guard, not a full shell parser)."""
    return any(tok in cmd for tok in SHELL_FORBIDDEN_SUBSTRINGS)


def plan_safe_shell_command_allowed(cmd: str) -> bool:
    """Same constraints as PLAN_MODE_TOOL_POLICY for run_shell (executor last-resort guard)."""
    if shell_command_has_forbidden_substrings(cmd):
        return False
    return shell_first_token_allowed(cmd, PLAN_MODE_ALLOWED_SHELL_FIRST)


def apply_tool_policy(engine: PlannerEngineOutput, policy: ToolPolicy) -> None:
    """
    Raise PlanValidationError if engine violates policy. No mutation.

    explore → tool must be explore; stop/replan → none; act → tool in allowed_act_tools,
    plus shell allowlist when applicable.
    """
    d = engine.decision
    tool = engine.tool

    eng_reason = (engine.reason or "").strip()

    if d == "explore":
        if tool != "explore":
            raise ToolPolicyViolationError(
                f'tool policy: decision "explore" requires tool "explore", got {tool!r}',
                policy_tool=str(tool),
                policy_reason=eng_reason,
            )
        return
    if d in ("stop", "replan"):
        if tool != "none":
            raise ToolPolicyViolationError(
                f'tool policy: decision "{d}" requires tool "none", got {tool!r}',
                policy_tool=str(tool),
                policy_reason=eng_reason,
            )
        return

    if d != "act":
        return

    if tool not in policy.allowed_act_tools:
        raise ToolPolicyViolationError(
            f"tool policy ({policy.mode} mode): act tool {tool!r} is not allowed; "
            f"allowed={sorted(policy.allowed_act_tools)}",
            policy_tool=str(tool),
            policy_reason=eng_reason,
        )

    if tool == "run_shell" and policy.shell_first_token_allowlist is not None:
        spec = engine.step
        if spec is None:
            raise ToolPolicyViolationError(
                "tool policy: run_shell requires non-null step",
                policy_tool="run_shell",
                policy_reason=eng_reason,
            )
        cmd = shell_command_from_step(spec)
        if shell_command_has_forbidden_substrings(cmd):
            raise ToolPolicyViolationError(
                "tool policy: run_shell command must not contain shell chaining or subshell tokens "
                f"{list(SHELL_FORBIDDEN_SUBSTRINGS)}; got {cmd!r}",
                policy_tool="run_shell",
                policy_reason=eng_reason,
                policy_command=cmd,
            )
        if not shell_first_token_allowed(cmd, policy.shell_first_token_allowlist):
            raise ToolPolicyViolationError(
                "tool policy: run_shell first command token must be one of "
                f"{sorted(policy.shell_first_token_allowlist)}; got {cmd!r}",
                policy_tool="run_shell",
                policy_reason=eng_reason,
                policy_command=cmd,
            )
