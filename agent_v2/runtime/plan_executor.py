"""
Phase 5–7 — Plan executor: plan-driven execution, per-step retry (Phase 6), replan loop (Phase 7).

Replanner is optional; when injected, exhausted step retries trigger a structured ReplanRequest,
a new PlanDocument (validated via PlanValidator), and an iterative continue (no unbounded recursion).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Union

from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.plan import PlanDocument, PlanStep, PlanStepLastResult
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.runtime.replanner import Replanner, merge_preserved_completed_steps
from agent_v2.runtime.tool_mapper import coerce_to_tool_result, map_tool_result_to_execution_result
from agent_v2.runtime.trace_emitter import TraceEmitter, TraceEmitterFactory
from agent_v2.validation.plan_validator import PlanValidator

_LOG = logging.getLogger(__name__)


def _execution_error_payload(err: ExecutionError | None) -> Any:
    if err is None:
        return None
    if hasattr(err, "model_dump"):
        return err.model_dump(mode="json")
    return str(err)


def _end_langfuse_step_span(span: Any, result: ExecutionResult | None) -> None:
    if span is None or result is None:
        return
    try:
        if result.metadata is not None:
            span.update(
                metadata={
                    "tool_name": result.metadata.tool_name,
                    "duration_ms": result.metadata.duration_ms,
                }
            )
        span.end(
            output={
                "success": result.success,
                "summary": result.output.summary if result.output else None,
                "error": _execution_error_payload(result.error),
            }
        )
    except Exception:
        pass


# Legacy ReAct dispatch expects uppercase internal actions (agent.core.actions.Action).
_PLAN_TO_LEGACY_ACTION: dict[str, str] = {
    "search": "SEARCH",
    "open_file": "READ",
    "edit": "EDIT",
    "run_tests": "RUN_TEST",
}

_DEFAULT_POLICY = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)


class PlanArgumentGeneratorProtocol(Protocol):
    def generate(self, step: PlanStep, state: Any) -> dict:
        ...


RunEvent = Union[
    tuple[str],  # ("completed",) | ("deadlock",) | ("aborted",)
    tuple[str, PlanStep, ExecutionResult],  # ("failed", step, result)
]


class PlanExecutor:
    """
    Controlled execution engine. PlanStep.action is fixed; argument_generator
    supplies tool arguments only.
    """

    def __init__(
        self,
        dispatcher: Any,
        argument_generator: PlanArgumentGeneratorProtocol,
        replanner: Optional[Replanner] = None,
        policy: Optional[ExecutionPolicy] = None,
        trace_emitter_factory: Optional[TraceEmitterFactory] = None,
    ):
        self.dispatcher = dispatcher
        self.argument_generator = argument_generator
        self.replanner = replanner
        self._policy = policy or _DEFAULT_POLICY
        self._trace_emitter_factory: TraceEmitterFactory = trace_emitter_factory or TraceEmitter
        self.trace_emitter: TraceEmitter = self._trace_emitter_factory()

    def run(self, plan: PlanDocument, state: Any) -> dict[str, Any]:
        """
        Execute until finish, deadlock, terminal failure, or replan budget exhausted.

        When a replanner is configured, a step failure after retries may produce a new
        validated PlanDocument; preserve_completed copies completed step execution state
        onto matching step_ids in the new plan.

        Returns ``{"status": "success"|"failed", "trace": Trace, "state": state}``.
        One TraceStep is recorded per plan step after final retry outcome (Phase 9).
        """
        if not isinstance(plan, PlanDocument):
            raise TypeError(f"PlanExecutor.run expected PlanDocument, got {type(plan).__name__}")
        if getattr(state, "current_plan", None) is None:
            raise ValueError(
                "PlanExecutor.run requires state.current_plan before execution "
                "(ModeManager must attach the plan first)."
            )

        self._run_start_mono = time.perf_counter()
        md0 = self._metadata_dict(state)
        md0["executor_dispatch_count"] = 0
        md0.pop("plan_executor_abort", None)

        self.trace_emitter = self._trace_emitter_factory()
        self._pin_active_plan(state, plan)
        work_plan = plan

        while True:
            work_plan = self._get_active_plan(state, work_plan)
            event = self._execute_scheduled_steps(work_plan, state)

            if event[0] == "completed":
                return self._finalize_run(state, plan, "success")
            if event[0] in ("deadlock", "aborted"):
                return self._finalize_run(state, plan, "failed")

            assert event[0] == "failed"
            _, failed_step, result = event
            self._record_failure_streak(state, result)

            if self.replanner is None:
                return self._finalize_run(state, plan, "failed")

            md = self._metadata_dict(state)
            max_r = self._max_replans(state)
            if int(md.get("replan_attempt", 0)) >= max_r:
                md["plan_executor_status"] = "failed_final"
                return self._finalize_run(state, plan, "failed")

            req = self.replanner.build_replan_request(state, work_plan, failed_step, result)
            lf = self._metadata_dict(state).get("langfuse_trace")
            if lf is not None and hasattr(lf, "event"):
                try:
                    et = req.failure_context.error.type
                    reason = et.value if hasattr(et, "value") else str(et)
                    lf.event(
                        name="replan_triggered",
                        metadata={
                            "failed_step_id": req.original_plan.failed_step_id,
                            "reason": reason,
                            "replan_id": req.replan_id,
                        },
                    )
                except Exception:
                    pass
            replan_res, new_plan = self.replanner.replan(req, langfuse_trace=lf)

            if replan_res.status != "success" or new_plan is None:
                md["plan_executor_status"] = "failed_final"
                if replan_res.validation.issues:
                    md["last_replan_issues"] = list(replan_res.validation.issues)
                return self._finalize_run(state, plan, "failed")

            md["replan_attempt"] = replan_res.metadata.replan_attempt

            if req.constraints.preserve_completed:
                new_plan = merge_preserved_completed_steps(work_plan, new_plan)
            PlanValidator.validate_plan(new_plan, policy=self._policy)

            work_plan = new_plan
            self._pin_active_plan(state, work_plan)

    def _finalize_run(self, state: Any, plan_for_id: PlanDocument, run_status: str) -> dict[str, Any]:
        active = self._get_active_plan(state, plan_for_id)
        trace = self.trace_emitter.build_trace(state.instruction, active.plan_id)
        md = self._metadata_dict(state)
        md["execution_trace_id"] = trace.trace_id
        md["plan_executor_run_status"] = run_status
        return {"status": run_status, "trace": trace, "state": state}

    def _max_replans(self, state: Any) -> int:
        pol = getattr(state, "execution_policy", None)
        if pol is not None and getattr(pol, "max_replans", None) is not None:
            return int(pol.max_replans)
        return int(self._policy.max_replans)

    @staticmethod
    def _metadata_dict(state: Any) -> dict:
        md = getattr(state, "metadata", None)
        if not isinstance(md, dict):
            return {}
        return md

    @staticmethod
    def _pin_active_plan(state: Any, plan: PlanDocument) -> None:
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            ctx["active_plan_document"] = plan

    @staticmethod
    def _get_active_plan(state: Any, fallback: PlanDocument) -> PlanDocument:
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            p = ctx.get("active_plan_document")
            if isinstance(p, PlanDocument):
                return p
        return fallback

    def _execute_scheduled_steps(self, plan: PlanDocument, state: Any) -> RunEvent:
        """
        One scheduling pass: dependency-gated rounds over steps until finish, failure, or no progress.
        """
        max_rounds = len(plan.steps) + 3
        ordered = sorted(plan.steps, key=lambda s: s.index)
        for _ in range(max_rounds):
            abort = self._guard_executor_limits(state)
            if abort:
                _LOG.error("PlanExecutor scheduling abort: %s", abort)
                self._metadata_dict(state)["plan_executor_abort"] = abort
                return ("aborted",)

            progressed = False
            for step in ordered:
                if step.execution.status == "completed":
                    continue
                if not self._can_execute(step, plan):
                    continue

                if not isinstance(step, PlanStep):
                    raise TypeError(f"Expected PlanStep, got {type(step).__name__}")

                state.plan_index = step.index

                if step.action == "finish":
                    lf_fin = self._metadata_dict(state).get("langfuse_trace")
                    if lf_fin is not None and hasattr(lf_fin, "span"):
                        try:
                            sp = lf_fin.span(
                                name=f"step_{step.index}_{step.action}",
                                input={
                                    "step_id": step.step_id,
                                    "goal": step.goal,
                                    "action": step.action,
                                },
                            )
                            sp.end(
                                output={
                                    "success": True,
                                    "summary": "Finished per plan.",
                                    "error": None,
                                }
                            )
                        except Exception:
                            pass
                    self._mark_step_completed_no_dispatch(step, success=True, summary="Finished per plan.")
                    fin = ExecutionResult(
                        step_id=step.step_id,
                        success=True,
                        status="success",
                        output=ExecutionOutput(summary="Finished per plan.", data={}),
                        error=None,
                        metadata=ExecutionMetadata(tool_name="finish", duration_ms=0, timestamp=self._utc_now()),
                    )
                    self._ensure_execution_result_contract(fin, step.step_id)
                    self.trace_emitter.record_step(step, fin, step.index)
                    self._update_state(state, step, "Finished per plan.")
                    return ("completed",)

                result = self._run_with_retry(step, state)
                self._ensure_execution_result_contract(result, step.step_id)
                self.trace_emitter.record_step(step, result, step.index)

                obs = ""
                if result.output is not None:
                    obs = str(result.output.summary or "")
                self._update_state(state, step, obs)
                progressed = True

                if not result.success:
                    return ("failed", step, result)

            if not progressed:
                break

        return ("deadlock",)

    def _can_execute(self, step: PlanStep, plan: PlanDocument) -> bool:
        completed = {s.step_id for s in plan.steps if s.execution.status == "completed"}
        return all(dep in completed for dep in (step.dependencies or []))

    def _run_with_retry(self, step: PlanStep, state: Any) -> ExecutionResult:
        """
        Dispatch the step up to max_attempts times. Owns execution.attempts increments
        (one per dispatch try). Semantics: attempts counts completed tries in this cycle.
        """
        max_attempts = max(1, int(step.execution.max_attempts))
        result: ExecutionResult | None = None
        md = self._metadata_dict(state)
        lf = md.get("langfuse_trace")
        step_span = None
        if lf is not None and hasattr(lf, "span"):
            try:
                step_span = lf.span(
                    name=f"step_{step.index}_{step.action}",
                    input={
                        "step_id": step.step_id,
                        "goal": step.goal,
                        "action": step.action,
                    },
                )
                md["_current_langfuse_span"] = step_span
            except Exception:
                step_span = None

        while step.execution.attempts < max_attempts:
            abort = self._guard_executor_limits(state)
            if abort is not None:
                _LOG.error("PlanExecutor dispatch abort: %s", abort)
                self._metadata_dict(state)["plan_executor_abort"] = abort
                now = self._utc_now()
                step.execution = step.execution.model_copy(
                    update={"status": "failed", "completed_at": now}
                )
                abort_res = ExecutionResult(
                    step_id=step.step_id,
                    success=False,
                    status="failure",
                    output=ExecutionOutput(summary=abort, data={}),
                    error=ExecutionError(type=ErrorType.unknown, message=abort),
                    metadata=ExecutionMetadata(
                        tool_name="plan_executor",
                        duration_ms=0,
                        timestamp=self._utc_now(),
                    ),
                )
                _end_langfuse_step_span(step_span, abort_res)
                md.pop("_current_langfuse_span", None)
                return abort_res

            now = self._utc_now()
            step.execution = step.execution.model_copy(
                update={
                    "started_at": step.execution.started_at or now,
                    "status": "in_progress",
                }
            )

            result = self._execute_step(step, state)
            self._ensure_execution_result_contract(result, step.step_id)
            step.execution = step.execution.model_copy(
                update={"attempts": step.execution.attempts + 1}
            )

            if result.success:
                completed_at = self._utc_now()
                summary = ""
                if result.output is not None:
                    summary = str(result.output.summary or "")
                lr = PlanStepLastResult(success=True, error=None, output_summary=summary)
                step.execution = step.execution.model_copy(
                    update={
                        "status": "completed",
                        "completed_at": completed_at,
                        "last_result": lr,
                    }
                )
                _end_langfuse_step_span(step_span, result)
                md.pop("_current_langfuse_span", None)
                return result

            if (
                lf is not None
                and hasattr(lf, "event")
                and step.execution.attempts < max_attempts
            ):
                try:
                    lf.event(
                        name="retry",
                        metadata={
                            "step_id": step.step_id,
                            "attempt": step.execution.attempts,
                            "error": _execution_error_payload(result.error),
                        },
                    )
                except Exception:
                    pass

            self._handle_failure(step, result)

            if step.execution.attempts > step.execution.max_attempts:
                raise RuntimeError(
                    f"Invariant violated: attempts {step.execution.attempts} > max_attempts "
                    f"{step.execution.max_attempts} for step {step.step_id}"
                )

        if result is None:
            nr = ExecutionResult(
                step_id=step.step_id,
                success=False,
                status="failure",
                output=ExecutionOutput(summary="no dispatch attempts executed", data={}),
                error=ExecutionError(type=ErrorType.unknown, message="exhausted attempts before dispatch"),
                metadata=ExecutionMetadata(tool_name="plan_executor", duration_ms=0, timestamp=self._utc_now()),
            )
            _end_langfuse_step_span(step_span, nr)
            md.pop("_current_langfuse_span", None)
            return nr
        completed_at = self._utc_now()
        step.execution = step.execution.model_copy(
            update={
                "status": "failed",
                "completed_at": completed_at,
            }
        )
        step.failure = step.failure.model_copy(update={"replan_required": True})
        _end_langfuse_step_span(step_span, result)
        md.pop("_current_langfuse_span", None)
        return result

    def _handle_failure(self, step: PlanStep, result: ExecutionResult) -> None:
        """
        Record failure from ExecutionResult; does not set terminal status (retry may follow).
        Recoverability is policy-driven later; today default True (not hardcoded error branches).
        """
        err = result.error
        err_type: ErrorType = err.type if err is not None else ErrorType.unknown
        summary = ""
        if result.output is not None:
            summary = str(result.output.summary or "")

        step.failure = step.failure.model_copy(
            update={
                "failure_type": err_type,
                "is_recoverable": True,
            }
        )
        lr = PlanStepLastResult(
            success=False,
            error=err_type.value,
            output_summary=summary,
        )
        step.execution = step.execution.model_copy(update={"last_result": lr})

    def _record_failure_streak(self, state: Any, result: ExecutionResult) -> None:
        """Optional metadata for replanner / diagnostics."""
        md = getattr(state, "metadata", None)
        if not isinstance(md, dict):
            return
        err = result.error
        err_type = err.type if err is not None else ErrorType.unknown
        md["failure_streak"] = int(md.get("failure_streak", 0)) + 1
        md["last_error"] = err_type.value

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _guard_executor_limits(self, state: Any) -> str | None:
        """Return abort reason string or None if within policy limits."""
        md = self._metadata_dict(state)
        cap = int(self._policy.max_executor_dispatches)
        n = int(md.get("executor_dispatch_count", 0))
        if n >= cap:
            return f"max_executor_dispatches ({cap}) reached"
        elapsed = time.perf_counter() - self._run_start_mono
        if elapsed > float(self._policy.max_runtime_seconds):
            return (
                f"max_runtime_seconds ({self._policy.max_runtime_seconds}s) exceeded "
                f"(elapsed {elapsed:.1f}s)"
            )
        return None

    @staticmethod
    def _ensure_execution_result_contract(result: ExecutionResult, step_id: str) -> None:
        if not isinstance(result, ExecutionResult):
            raise TypeError(f"Expected ExecutionResult for step {step_id}, got {type(result).__name__}")
        if result.output is None or not str(result.output.summary or "").strip():
            raise ValueError(f"ExecutionResult for step {step_id} must include output.summary")

    def _execute_step(self, step: PlanStep, state: Any) -> ExecutionResult:
        if not isinstance(step, PlanStep):
            raise TypeError(f"PlanExecutor._execute_step requires PlanStep, got {type(step).__name__}")

        md = self._metadata_dict(state)
        md["executor_dispatch_count"] = int(md.get("executor_dispatch_count", 0)) + 1

        args = self.argument_generator.generate(step, state)
        merged = self._merge_args(step, args)

        if step.action == "shell":
            result = self._dispatch_shell(step.step_id, merged, state)
            self._ensure_execution_result_contract(result, step.step_id)
            return result

        dispatch_dict = self._to_dispatch_step(step, merged)
        result = self.dispatcher.execute(dispatch_dict, state)
        self._ensure_execution_result_contract(result, step.step_id)
        return result

    def _merge_args(self, step: PlanStep, generated: dict) -> dict:
        base: dict = {}
        if isinstance(step.inputs, dict):
            for key in ("path", "query", "instruction", "command", "file"):
                val = step.inputs.get(key)
                if val is not None and str(val).strip():
                    base[key] = val
        out = {**base}
        if isinstance(generated, dict):
            out.update(generated)
        return out

    def _to_dispatch_step(self, step: PlanStep, args: dict) -> dict:
        """Build the legacy ReAct step dict expected by _dispatch_react + Dispatcher."""
        pa = step.action
        legacy = _PLAN_TO_LEGACY_ACTION.get(pa)
        if legacy is None:
            raise ValueError(f"Unsupported plan action for ReAct dispatch: {pa!r}")

        row: dict[str, Any] = {
            "id": step.index,
            "step_id": step.step_id,
            "action": legacy,
            "artifact_mode": "code",
            "_react_thought": "",
            "_react_action_raw": pa,
            "_react_args": args,
        }
        if pa == "search":
            row["query"] = args.get("query", "")
            row["description"] = row["query"]
        elif pa == "open_file":
            row["path"] = args.get("path", "")
            row["description"] = row["path"]
        elif pa == "edit":
            row["path"] = args.get("path", "")
            row["edit_target_path"] = args.get("path", "")
            row["description"] = args.get("instruction", "")
        elif pa == "run_tests":
            row["description"] = ""
        return row

    def _dispatch_shell(self, step_id: str, args: dict, state: Any) -> ExecutionResult:
        """Shell is not in the ReAct registry; run via injected Shell primitive."""
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            tr = coerce_to_tool_result(
                {"success": False, "output": {}, "error": "shell requires non-empty command"},
                tool_name="shell",
            )
            return map_tool_result_to_execution_result(tr, step_id=step_id)

        shell = None
        if getattr(state, "context", None) is not None:
            shell = state.context.get("shell")
        if shell is None:
            from agent_v2.primitives.shell import Shell  # noqa: PLC0415

            shell = Shell()
            if getattr(state, "context", None) is not None:
                state.context["shell"] = shell

        raw = shell.run(cmd)
        tool_result = coerce_to_tool_result(raw, tool_name="shell")
        return map_tool_result_to_execution_result(tool_result, step_id=step_id)

    def _mark_step_completed_no_dispatch(self, step: PlanStep, *, success: bool, summary: str) -> None:
        now = self._utc_now()
        lr = PlanStepLastResult(success=success, error=None, output_summary=summary)
        step.execution = step.execution.model_copy(
            update={
                "attempts": step.execution.attempts + 1,
                "status": "completed",
                "started_at": step.execution.started_at or now,
                "completed_at": now,
                "last_result": lr,
            }
        )

    def _update_state(self, state: Any, step: PlanStep, summary: str) -> None:
        if not hasattr(state, "history"):
            return
        state.history.append(
            {
                "step_id": step.step_id,
                "plan_action": step.action,
                "action": step.action,
                "observation": summary,
            }
        )
        if hasattr(state, "step_results"):
            state.step_results.append(
                {
                    "step_id": step.step_id,
                    "action": step.action,
                    "result_summary": summary,
                }
            )
        if len(state.history) != len(state.step_results):
            raise RuntimeError(
                "AgentState invariant violated: len(history) must equal len(step_results)"
            )
