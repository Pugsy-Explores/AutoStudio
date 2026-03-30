"""
Stage 32 — Suite loading and mode helpers.

Responsibilities:
- load_suite(name) — load specs by suite name
- load_specs_for_mode(suite_name, execution_mode) — mode-specific loading
"""

from __future__ import annotations

from tests.agent_eval.execution_mode import is_suite_loading_mode


def load_suite(name: str):
    """Load task specs by suite name. Used for mocked and generic loading."""
    if name == "core12":
        from tests.agent_eval.suites.core12 import load_core12

        return load_core12()
    if name == "audit12":
        from tests.agent_eval.suites.audit12 import load_audit12_specs

        return load_audit12_specs()
    if name == "holdout8":
        from tests.agent_eval.suites.holdout8 import load_holdout8_specs

        return load_holdout8_specs()
    if name == "adversarial12":
        from tests.agent_eval.suites.adversarial12 import load_adversarial12_specs

        return load_adversarial12_specs()
    if name == "external6":
        from tests.agent_eval.suites.external6 import load_external6_specs

        return load_external6_specs()
    if name == "live4":
        from tests.agent_eval.suites.live4 import load_live4_specs

        return load_live4_specs()
    if name == "audit6":
        from tests.agent_eval.suites.audit6 import load_audit6_specs

        return load_audit6_specs()
    if name == "paired4":
        from tests.agent_eval.suites.paired4 import load_paired4_specs

        return load_paired4_specs(evaluation_kind="execution_regression")
    if name == "paired8":
        from tests.agent_eval.suites.paired8 import load_paired8_specs

        return load_paired8_specs(evaluation_kind="execution_regression")
    if name == "routing_contract":
        from tests.agent_eval.suites.routing_contract import load_routing_contract_specs

        return load_routing_contract_specs()
    if name == "search_stack":
        from tests.agent_eval.suites.search_stack import load_search_stack_specs

        return load_search_stack_specs()
    raise SystemExit(
        f"unknown suite: {name!r} (try core12, audit12, audit6, holdout8, adversarial12, external6, live4, paired4, paired8, routing_contract, search_stack)"
    )


def load_specs_for_mode(suite_name: str, execution_mode: str):
    """Load specs for given suite and execution mode. Handles mode-specific mapping."""
    if not is_suite_loading_mode(execution_mode):
        return load_suite(suite_name)
    if suite_name == "audit12":
        from tests.agent_eval.suites.audit12 import load_audit12_specs

        return load_audit12_specs()
    if suite_name == "holdout8":
        from tests.agent_eval.suites.holdout8 import load_holdout8_specs

        return load_holdout8_specs()
    if suite_name == "adversarial12":
        from tests.agent_eval.suites.adversarial12 import load_adversarial12_specs

        return load_adversarial12_specs()
    if suite_name == "external6":
        from tests.agent_eval.suites.external6 import load_external6_specs

        return load_external6_specs()
    if suite_name == "live4":
        from tests.agent_eval.suites.live4 import load_live4_specs

        return load_live4_specs()
    if suite_name == "paired4":
        from tests.agent_eval.suites.paired4 import load_paired4_specs

        return load_paired4_specs(
            evaluation_kind="full_agent" if execution_mode == "live_model" else "execution_regression"
        )
    if suite_name == "paired8":
        from tests.agent_eval.suites.paired8 import load_paired8_specs

        return load_paired8_specs(
            evaluation_kind="full_agent" if execution_mode == "live_model" else "execution_regression"
        )
    if suite_name == "routing_contract":
        from tests.agent_eval.suites.routing_contract import load_routing_contract_specs

        return load_routing_contract_specs(
            evaluation_kind="full_agent" if execution_mode == "live_model" else "execution_regression"
        )
    if suite_name == "search_stack":
        from tests.agent_eval.suites.search_stack import load_search_stack_specs

        return load_search_stack_specs(
            evaluation_kind="full_agent" if execution_mode == "live_model" else "execution_regression"
        )
    from tests.agent_eval.suites.audit6 import load_audit6_specs

    return load_audit6_specs()
