"""Architecture map: classify modules into controllers, services, data_layers, utilities."""

import logging
import re

from agent.models.model_client import call_small_model
from config.repo_intelligence_config import MAX_ARCHITECTURE_NODES

logger = logging.getLogger(__name__)

_ARCHITECTURE_SYSTEM = """You classify Python modules into one of: controllers, services, data_layers, utilities.
- controllers: API handlers, HTTP routes, views, web entry points
- services: business logic, orchestration, core domain logic
- data_layers: models, repositories, DB access, persistence
- utilities: helpers, config, shared utils, common code

Reply with exactly one word per line: module_name: layer
Example: agent.api.routes: controllers"""

_LAYER_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "controllers": [
        ("api", re.compile(r"\bapi\b", re.I)),
        ("controller", re.compile(r"\bcontroller\b", re.I)),
        ("route", re.compile(r"\broute\b", re.I)),
        ("view", re.compile(r"\bview\b", re.I)),
        ("handler", re.compile(r"\bhandler\b", re.I)),
        ("webhook", re.compile(r"\bwebhook\b", re.I)),
    ],
    "services": [
        ("service", re.compile(r"\bservice\b", re.I)),
        ("orchestrator", re.compile(r"\borchestrator\b", re.I)),
        ("executor", re.compile(r"\bexecutor\b", re.I)),
        ("dispatcher", re.compile(r"\bdispatcher\b", re.I)),
    ],
    "data_layers": [
        ("model", re.compile(r"\bmodel\b", re.I)),
        ("repo", re.compile(r"\brepo\b", re.I)),
        ("repository", re.compile(r"\brepository\b", re.I)),
        ("db", re.compile(r"\bdb\b", re.I)),
        ("storage", re.compile(r"\bstorage\b", re.I)),
        ("data", re.compile(r"\bdata\b", re.I)),
    ],
    "utilities": [
        ("util", re.compile(r"\butil\b", re.I)),
        ("utils", re.compile(r"\butils\b", re.I)),
        ("helper", re.compile(r"\bhelper\b", re.I)),
        ("config", re.compile(r"\bconfig\b", re.I)),
        ("common", re.compile(r"\bcommon\b", re.I)),
    ],
}


def _classify_by_heuristic(module_name: str) -> str | None:
    """Classify module by path/name heuristics. Returns layer or None if ambiguous."""
    name = (module_name or "").lower()
    for layer, patterns in _LAYER_PATTERNS.items():
        for _, pat in patterns:
            if pat.search(name):
                return layer
    return None


def build_architecture_map(repo_summary: dict) -> dict:
    """
    Classify modules into controllers, services, data_layers, utilities.
    Uses heuristics first; call_small_model for ambiguous cases.
    Capped at MAX_ARCHITECTURE_NODES.
    """
    modules = repo_summary.get("modules") or []
    if len(modules) > MAX_ARCHITECTURE_NODES:
        modules = modules[:MAX_ARCHITECTURE_NODES]
        logger.warning(
            "[architecture_map] capping: %d modules > MAX_ARCHITECTURE_NODES=%d",
            len(repo_summary.get("modules", [])),
            MAX_ARCHITECTURE_NODES,
        )

    controllers: list[str] = []
    services: list[str] = []
    data_layers: list[str] = []
    utilities: list[str] = []
    ambiguous: list[str] = []

    for m in modules:
        name = m.get("name", "")
        layer = _classify_by_heuristic(name)
        if layer == "controllers":
            controllers.append(name)
        elif layer == "services":
            services.append(name)
        elif layer == "data_layers":
            data_layers.append(name)
        elif layer == "utilities":
            utilities.append(name)
        else:
            ambiguous.append(name)

    if ambiguous:
        try:
            prompt = (
                f"Classify these modules (one per line, format 'module: layer'):\n"
                + "\n".join(ambiguous[:50])
            )
            resp = call_small_model(
                prompt,
                max_tokens=512,
                task_name="query_rewrite",
                system_prompt=_ARCHITECTURE_SYSTEM,
            )
            for line in (resp or "").strip().splitlines():
                line = line.strip()
                if ":" not in line:
                    continue
                mod, rest = line.split(":", 1)
                mod = mod.strip()
                layer = rest.strip().lower()
                if mod in ambiguous:
                    if "controller" in layer or "api" in layer:
                        controllers.append(mod)
                    elif "service" in layer:
                        services.append(mod)
                    elif "data" in layer or "model" in layer or "repo" in layer:
                        data_layers.append(mod)
                    else:
                        utilities.append(mod)
        except Exception as e:
            logger.warning("[architecture_map] model fallback failed: %s; treating ambiguous as utilities", e)
            utilities.extend(ambiguous)

    result = {
        "controllers": controllers,
        "services": services,
        "data_layers": data_layers,
        "utilities": utilities,
    }
    logger.info(
        "[architecture_map] controllers=%d services=%d data_layers=%d utilities=%d",
        len(controllers),
        len(services),
        len(data_layers),
        len(utilities),
    )
    return result
