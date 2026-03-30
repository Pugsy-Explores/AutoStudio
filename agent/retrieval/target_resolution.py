"""
Stage 25: Target resolution layer.

Separates edit targets from validation targets. Uses instruction text, validation
commands, repo structure, and file content to classify paths before diff planning.
Generic, no task-id-specific logic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Patterns that strongly indicate validation/assertion scripts (demote as edit targets)
# Stage 28: add test_*.py, *_test.py anywhere; bin/assert_*, scripts/check_*, scripts/verify_*
_VALIDATION_SCRIPT_PATTERNS = (
    r"scripts/assert_\w+\.py",
    r"scripts/check_\w+\.py",
    r"scripts/verify_\w+\.py",
    r"bin/assert_\w+\.py",
    r"bin/check_\w+\.py",
    r"bin/verify_\w+\.py",
    r"tests/test_\w+\.py",
    r"tests/.*_test\.py",
    r".*/test_[^/]+\.py",  # test_*.py in any directory
    r".*[^/]_test\.py",   # *_test.py in any directory
)
_VALIDATION_PATTERN_RE = re.compile(
    "|".join(f"({p})" for p in _VALIDATION_SCRIPT_PATTERNS)
)


def is_validation_script_path(file_path: str) -> bool:
    """
    True if path matches validation/assert/check/verify script patterns.
    These should be demoted as edit targets unless instruction explicitly says to modify them.
    """
    if not file_path or not isinstance(file_path, str):
        return False
    fn = file_path.replace("\\", "/").strip()
    return bool(_VALIDATION_PATTERN_RE.search(fn))


def validation_script_paths_from_instruction(instruction: str) -> list[str]:
    """Extract validation script paths mentioned in the instruction."""
    if not instruction:
        return []
    out: list[str] = []
    for pat in (
        r"\b(bin/assert_[\w]+\.py)\b",
        r"\b(bin/check_[\w]+\.py)\b",
        r"\b(bin/verify_[\w]+\.py)\b",
        r"\b(scripts/assert_[\w]+\.py)\b",
        r"\b(scripts/check_[\w]+\.py)\b",
        r"\b(scripts/verify_[\w]+\.py)\b",
    ):
        for m in re.finditer(pat, instruction):
            p = m.group(1).strip()
            if p and p not in out:
                out.append(p)
    return out


def validation_script_paths_from_command(cmd: str | None) -> list[str]:
    """Extract validation script path from command (e.g. python3 bin/assert_foo.py)."""
    if not cmd or not isinstance(cmd, str):
        return []
    out: list[str] = []
    for m in re.finditer(r"[\w./\\]+\.py", cmd):
        p = m.group(0).replace("\\", "/")
        if is_validation_script_path(p) and p not in out:
            out.append(p)
    return out


def _imports_from_python_file(file_path: str, project_root: str) -> list[str]:
    """
    Parse Python file and return imported module names (from X import Y -> X).
    Bounded, deterministic. Returns dot-separated module paths.
    """
    if not file_path or not project_root:
        return []
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(project_root) / file_path
    if not p.is_file() or p.suffix.lower() not in (".py", ".pyi"):
        return []
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    modules: list[str] = []
    for m in re.finditer(r"from\s+([\w.]+)\s+import", content):
        mod = m.group(1).strip()
        if mod and mod not in modules:
            modules.append(mod)
    for m in re.finditer(r"import\s+([\w.]+)\b", content):
        mod = m.group(1).strip()
        if mod and mod not in modules:
            modules.append(mod)
    return modules[:20]


def _module_to_file_path(module_name: str, project_root: str) -> str | None:
    """Convert module name (e.g. validation.guard) to project-relative file path."""
    if not module_name or not project_root:
        return None
    root = Path(project_root).resolve()
    parts = module_name.split(".")
    if not parts:
        return None
    # Try package/__init__.py or package/module.py
    rel = "/".join(parts[:-1]) if len(parts) > 1 else parts[0]
    stem = parts[-1]
    candidates = [
        f"{rel}/{stem}.py",
        f"{rel}/__init__.py",
        f"{rel}/{stem}/__init__.py",
    ]
    for cand in candidates:
        p = root / cand
        if p.is_file():
            try:
                return str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                pass
    # Fallback: single part -> module.py
    p = root / f"{stem}.py"
    if p.is_file():
        try:
            return str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            pass
    return None


def inferred_source_files_from_validation(
    validation_path: str,
    project_root: str,
) -> list[tuple[str, str]]:
    """
    Parse validation script imports and return (file_path, evidence) for each source file.
    Evidence describes why this file was inferred (e.g. "imported_by_validation").
    """
    if not validation_path or not project_root:
        return []
    modules = _imports_from_python_file(validation_path, project_root)
    out: list[tuple[str, str]] = []
    for mod in modules:
        fp = _module_to_file_path(mod, project_root)
        if fp:
            out.append((fp, f"imported_by_{Path(validation_path).name}"))
    return out


def resolve_module_descriptor_to_files(
    instruction: str,
    project_root: str,
) -> list[tuple[str, str]]:
    """
    Infer source files from module descriptors in instruction.
    E.g. "runtime options module" -> runtime/options.py
    "config defaults" -> cfg/defaults.py or config/defaults.py
    Returns list of (file_path, evidence).
    """
    if not instruction or not project_root:
        return []
    root = Path(project_root).resolve()
    low = instruction.lower()
    out: list[tuple[str, str]] = []

    # Extract noun phrases that might be module descriptors
    # "runtime options module" -> ["runtime", "options"]
    # "config defaults" -> ["config", "defaults"]
    phrases = re.findall(r"\b([a-z][a-z0-9_]*)\s+([a-z][a-z0-9_]*)\s+module\b", low)
    for a, b in phrases:
        cand = f"{a}/{b}.py"
        p = root / cand
        if p.is_file():
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
                if (rel, "module_descriptor") not in out:
                    out.append((rel, "module_descriptor"))
            except ValueError:
                pass
        # Also try cfg/ for config, impl/ for impl
        for prefix in ("cfg", "config", "impl", "core", "runtime", "validation", "logging"):
            if a == prefix or (a in ("config", "cfg") and prefix in ("cfg", "config")):
                cand2 = f"{prefix}/{b}.py"
                p2 = root / cand2
                if p2.is_file():
                    try:
                        rel = str(p2.relative_to(root)).replace("\\", "/")
                        if not any(r[0] == rel for r in out):
                            out.append((rel, "module_descriptor"))
                    except ValueError:
                        pass

    # Two-word phrases without "module": "config defaults", "runtime options", etc.
    # Generic directory names only (no benchmark-specific tokens).
    phrases2 = re.findall(r"\b(validation|runtime|config|cfg|logging|parser|options|defaults)\s+(\w+)\b", low)
    for a, b in phrases2:
        if b in ("module", "file", "script"):
            continue
        for dir_name, file_stem in [(a, b), (b, a)]:
            cand = f"{dir_name}/{file_stem}.py"
            p = root / cand
            if p.is_file():
                try:
                    rel = str(p.relative_to(root)).replace("\\", "/")
                    if not any(r[0] == rel for r in out):
                        out.append((rel, "descriptor_phrase"))
                except ValueError:
                    pass

    # Path hints: explicit path literals in instruction (e.g. lib/version.py, README.md)
    for m in re.finditer(r"\b([\w./]+\.(?:py|pyi|md))\b", instruction, re.I):
        cand = m.group(1).strip().replace("\\", "/")
        if "/" in cand or cand.startswith(("README", "CHANGELOG", "API")):
            p = root / cand
            if p.is_file():
                try:
                    rel = str(p.relative_to(root)).replace("\\", "/")
                    if not any(r[0] == rel for r in out):
                        out.append((rel, "path_hint_descriptor"))
                except ValueError:
                    pass

    return out[:10]


def rank_edit_targets(
    instruction: str,
    project_root: str,
    validation_command: str | None,
    explicit_paths: list[str],
    candidate_paths: list[str],
    context: dict | None = None,
) -> list[tuple[str, int, str]]:
    """
    Rank candidate edit targets. Returns list of (file_path, rank_penalty, evidence).
    Lower rank_penalty = better. 0 = explicit edit target, 100 = validation script (demoted).
    """
    from agent.retrieval.task_semantics import instruction_asks_to_modify_validation_script

    ctx = context or {}
    root = Path(project_root).resolve()
    results: list[tuple[str, int, str]] = []

    # Validation scripts from instruction and command
    val_from_inst = validation_script_paths_from_instruction(instruction)
    val_from_cmd = validation_script_paths_from_command(validation_command)
    validation_paths = set(val_from_inst + val_from_cmd)

    # Inferred source files from validation script imports
    inferred_sources: dict[str, str] = {}
    for vp in validation_paths:
        for fp, ev in inferred_source_files_from_validation(vp, project_root):
            if fp not in inferred_sources:
                inferred_sources[fp] = ev

    # Module descriptor resolution (when no explicit paths)
    descriptor_files: dict[str, str] = {}
    if not explicit_paths:
        for fp, ev in resolve_module_descriptor_to_files(instruction, project_root):
            if fp not in descriptor_files:
                descriptor_files[fp] = ev

    # Build candidate set
    all_candidates = set(explicit_paths) | set(candidate_paths) | set(inferred_sources) | set(descriptor_files)
    for fp in all_candidates:
        try:
            p = root / fp
            if not p.is_file():
                continue
        except (OSError, ValueError):
            continue

        norm = fp.replace("\\", "/")
        penalty = 50
        evidence = "candidate"

        if norm in explicit_paths and not instruction_asks_to_modify_validation_script(instruction):
            penalty = 0
            evidence = "explicit_edit_target"
        elif norm in inferred_sources:
            penalty = 5
            evidence = inferred_sources[norm]
            if is_validation_script_path(norm):
                penalty = 100
        elif norm in descriptor_files:
            penalty = 10
            evidence = descriptor_files[norm]
        elif is_validation_script_path(norm):
            if instruction_asks_to_modify_validation_script(instruction):
                penalty = 20
                evidence = "validation_script_explicitly_requested"
            else:
                penalty = 100
                evidence = "validation_script_demoted"
        elif norm in explicit_paths:
            penalty = 15
            evidence = "hint"

        results.append((norm, penalty, evidence))

    results.sort(key=lambda x: (x[1], x[0]))
    return results[:MAX_RANKED_TARGETS]


MAX_RANKED_TARGETS = 15

# Stdlib module names that commonly shadow local packages
_STDLIB_SHADOW_CANDIDATES = frozenset({"io", "logging", "config", "parser", "ast", "types"})


def detect_likely_import_shadowing(validation_output: str | None) -> dict[str, Any]:
    """
    Parse validation failure output for import errors. Returns telemetry dict with:
    - import_errors_detected: list of (module_name, error_type)
    - likely_stdlib_shadowing: True if any module is a known stdlib shadow candidate
    - module_names_in_error: list of module names mentioned in ImportError/ModuleNotFoundError
    """
    if not validation_output or not isinstance(validation_output, str):
        return {}
    out: dict[str, Any] = {
        "import_errors_detected": [],
        "likely_stdlib_shadowing": False,
        "module_names_in_error": [],
    }
    # ModuleNotFoundError: No module named 'X'
    for m in re.finditer(r"No module named ['\"]?([\w.]+)['\"]?", validation_output):
        mod = m.group(1).split(".")[0]
        if mod and mod not in out["module_names_in_error"]:
            out["module_names_in_error"].append(mod)
            out["import_errors_detected"].append((mod, "ModuleNotFoundError"))
    # ImportError: cannot import name 'X' from 'Y'
    for m in re.finditer(r"from ['\"]?([\w.]+)['\"]?", validation_output):
        mod = m.group(1).split(".")[0]
        if mod and mod not in out["module_names_in_error"]:
            out["module_names_in_error"].append(mod)
            out["import_errors_detected"].append((mod, "ImportError"))
    if out["module_names_in_error"]:
        out["likely_stdlib_shadowing"] = any(
            m in _STDLIB_SHADOW_CANDIDATES for m in out["module_names_in_error"]
        )
    return out


def resolve_edit_targets_for_plan(
    instruction: str,
    project_root: str,
    context: dict,
) -> dict[str, Any]:
    """
    Full target resolution for diff planning.
    Returns dict with:
      - edit_targets_ranked: list of (path, penalty, evidence)
      - validation_scripts: paths to demote
      - inferred_sources: path -> evidence
      - module_descriptor_sources: path -> evidence
      - target_resolution_telemetry: for RCA
    """
    val_cmd = (
        context.get("resolved_validation_command")
        or context.get("validation_command")
        or context.get("requested_validation_target")
    )
    from agent.retrieval.task_semantics import instruction_edit_target_paths, instruction_path_hints

    explicit = instruction_edit_target_paths(instruction)
    hints = instruction_path_hints(instruction)

    # Collect candidate paths from hints (excluding validation scripts when we have inferred sources)
    val_paths = set(
        validation_script_paths_from_instruction(instruction)
        + validation_script_paths_from_command(val_cmd)
    )
    inferred: dict[str, str] = {}
    for vp in val_paths:
        for fp, ev in inferred_source_files_from_validation(vp, project_root):
            if fp not in inferred:
                inferred[fp] = ev

    descriptor: dict[str, str] = {}
    if not explicit:
        for fp, ev in resolve_module_descriptor_to_files(instruction, project_root):
            if fp not in descriptor:
                descriptor[fp] = ev

    candidates = list(set(hints) | set(inferred) | set(descriptor))
    ranked = rank_edit_targets(
        instruction,
        project_root,
        val_cmd,
        explicit,
        candidates,
        context,
    )

    # Retry override: when symbol_retry provides edit_target_file_override, prepend it
    override = context.get("edit_target_file_override")
    if override and isinstance(override, str):
        root = Path(project_root).resolve()
        p = root / override if not Path(override).is_absolute() else Path(override)
        try:
            if p.is_file():
                rel = str(p.relative_to(root)).replace("\\", "/")
                ranked = [(rel, 0, "retry_override")] + [r for r in ranked if r[0] != rel]
        except (ValueError, OSError):
            pass

    # EDIT_BINDING fallback: when ranked is empty, use edit_binding from ranked_context[0]
    if not ranked:
        binding = context.get("edit_binding")
        if isinstance(binding, dict) and binding.get("file"):
            root = Path(project_root).resolve()
            fp = binding["file"]
            p = Path(fp) if Path(fp).is_absolute() else root / fp
            try:
                if p.is_file():
                    rel = str(p.relative_to(root)).replace("\\", "/")
                    if not is_validation_script_path(rel):
                        ranked = [(rel, 0, "edit_binding")]
            except (ValueError, OSError):
                pass

    # Stage 28: telemetry for source vs validator preference
    top_path = ranked[0][0] if ranked else ""
    source_preferred = (
        top_path
        and top_path not in val_paths
        and not is_validation_script_path(top_path)
        and (explicit or inferred or descriptor)
    )
    return {
        "edit_targets_ranked": ranked,
        "validation_scripts": list(val_paths),
        "inferred_sources": inferred,
        "module_descriptor_sources": descriptor,
        "target_resolution_telemetry": {
            "explicit_path_count": len(explicit),
            "inferred_source_count": len(inferred),
            "descriptor_resolved_count": len(descriptor),
            "validation_script_count": len(val_paths),
            "source_file_preferred_over_validator": source_preferred,
        },
    }
