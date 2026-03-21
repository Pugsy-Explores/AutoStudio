"""Stage 24: Grounded patch construction layer.

All strategies are content-driven: they work from instruction text and actual file
content. No task_id-specific logic, no benchmark name hardcoding.

Every candidate carries evidence (matched source lines / symbol / constant) so
the executor can reject ungrounded patches before they are applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_EVIDENCE_LEN = 200
MAX_CANDIDATES = 4


@dataclass
class PatchCandidate:
    patch: dict               # executor-ready patch dict (action + params)
    strategy: str             # e.g. "return_binary_op_repair"
    evidence_type: str        # e.g. "matched_return_op_line"
    evidence_excerpt: str     # bounded excerpt of matched file text
    rank: int                 # lower is better (0 = highest confidence)
    extra: dict = field(default_factory=dict)

    def has_evidence(self) -> bool:
        return bool(self.evidence_type and self.evidence_excerpt.strip())

    def telemetry(self) -> dict[str, Any]:
        out = {
            "patch_candidate_strategy": self.strategy,
            "patch_candidate_evidence_type": self.evidence_type,
            "patch_candidate_evidence_excerpt": self.evidence_excerpt[:MAX_EVIDENCE_LEN],
        }
        if "semantic_match_score" in self.extra:
            out["candidate_semantic_match_score"] = self.extra["semantic_match_score"]
        if "requested_symbol_name" in self.extra:
            out["requested_symbol_name"] = self.extra["requested_symbol_name"]
        if "requested_return_value" in self.extra:
            out["requested_return_value"] = self.extra["requested_return_value"]
        return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_grounded_candidates(
    instruction: str,
    file_path: str,
    file_content: str,
    project_root: str,
) -> list[PatchCandidate]:
    """
    Generate ordered list of grounded patch candidates for (instruction, file).
    Returns up to MAX_CANDIDATES candidates ranked by evidence strength.
    All strategies are generic and content-driven.
    """
    candidates: list[PatchCandidate] = []

    # Strategy 1 — fix wrong binary operator in return statement
    c = _try_return_binary_op_repair(instruction, file_content)
    if c:
        candidates.append(c)

    # Strategy 1b — fix existing function return value (e.g. "fix get_timeout so it returns 30")
    c = _try_fix_return_value(instruction, file_content)
    if c:
        candidates.append(c)

    # Strategy 2 — negate inverted empty check (len(s) == 0 → len(s) > 0)
    c = _try_empty_check_negation(instruction, file_content)
    if c:
        candidates.append(c)

    # Strategy 3 — replace plain return with split() call
    c = _try_raw_return_to_split(instruction, file_content)
    if c:
        candidates.append(c)

    # Strategy 4 — rename string constant from old value to new value
    c = _try_string_constant_rename(instruction, file_content)
    if c:
        candidates.append(c)

    # Strategy 5 — align version constant in .py to paired .md header
    c = _try_version_constant_align(instruction, file_path, file_content, project_root)
    if c:
        candidates.append(c)

    # Strategy 6 — align URL constant in .py to paired .md bold URL
    c = _try_url_constant_align(instruction, file_path, file_content, project_root)
    if c:
        candidates.append(c)

    # Strategy 7 — add a missing function when explicitly named in instruction
    c = _try_add_missing_function(instruction, file_content, Path(file_path).suffix.lower())
    if c:
        candidates.append(c)

    # Stage 26: semantic ranking — prefer candidates most aligned with instruction
    candidates = _apply_semantic_ranking(instruction, candidates)
    candidates.sort(key=lambda x: (x.rank, -x.extra.get("semantic_match_score", 0)))
    return candidates[:MAX_CANDIDATES]


def select_best_candidate(candidates: list[PatchCandidate], instruction: str = "") -> PatchCandidate | None:
    """Return highest-ranked candidate (lowest rank, then highest semantic_match_score), or None."""
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (c.rank, -c.extra.get("semantic_match_score", 0)),
    )


def validate_grounded_candidate(
    candidate: PatchCandidate,
    file_content: str,
) -> tuple[bool, str | None]:
    """
    Pre-executor sanity checks.
    Returns (ok, reject_reason).  reject_reason is None when ok.
    """
    if not candidate.has_evidence():
        return False, "no_grounded_evidence"

    patch = candidate.patch
    action = patch.get("action")

    if action == "text_sub":
        old = str(patch.get("old", ""))
        new = str(patch.get("new", ""))
        if not old.strip():
            return False, "empty_patch"
        if old not in file_content:
            return False, "target_region_not_found"
        if old == new:
            return False, "no_effect_change"
        return True, None

    if action == "insert" and patch.get("target_node") == "module_append":
        code = patch.get("code", "")
        if not code or not code.strip():
            return False, "empty_patch"
        return True, None

    return True, None


def validate_semantic_grounded_candidate(
    candidate: PatchCandidate,
    instruction: str,
) -> tuple[bool, str | None]:
    """
    Stage 26: Semantic post-generation checks before execution.
    Reject candidates that do not implement what the instruction requests.
    Returns (ok, reject_reason).  reject_reason is None when ok.
    """
    low = instruction.lower()
    patch = candidate.patch
    action = patch.get("action")

    # Add fname(): instruction says "Add FNAME()" — candidate must define FNAME
    if "add " in low and "()" in instruction:
        m = re.search(r"\bAdd\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*\)", instruction, re.I)
        if m:
            requested_fname = m.group(1)
            code = patch.get("code", "") if action == "insert" else ""
            new = patch.get("new", "") if action == "text_sub" else ""
            combined = code + new
            if f"def {requested_fname}(" not in combined and f"def {requested_fname} (" not in combined:
                return False, "requested_symbol_not_implemented"

    # Return X: instruction says "return X" or "returning X" — patch must produce X
    ret_m = re.search(
        r"(?:return|returning)\s+(?:a\s+)?(?:non-empty\s+)?(?:string\s+)?(?:\(?\s*e\.g\.\s+)?['\"]([^'\"]+)['\"]",
        instruction,
        re.I,
    )
    if ret_m:
        requested_literal = ret_m.group(1)
        code = patch.get("code", "") if action == "insert" else ""
        new = patch.get("new", "") if action == "text_sub" else ""
        combined = code + new
        if f'"{requested_literal}"' not in combined and f"'{requested_literal}'" not in combined:
            # Allow if instruction said "e.g." (example) and we have some non-empty string
            if "e.g." in instruction.lower():
                if 'return ""' in combined or "return ''" in combined:
                    return False, "requested_literal_not_realized"
            else:
                return False, "requested_literal_not_realized"

    # Fix F so it returns N (numeric): patch must contain return N
    ret_num_m = re.search(
        r"\b(?:fix|make)\s+[a-zA-Z_]\w*\s*(?:\(\))?\s*(?:so\s+it\s+)?returns?\s+([0-9]+)\b",
        instruction,
        re.I,
    )
    if ret_num_m:
        requested_num = ret_num_m.group(1)
        code = patch.get("code", "") if action == "insert" else ""
        new = patch.get("new", "") if action == "text_sub" else ""
        combined = code + new
        if f"return {requested_num}" not in combined and f"return{requested_num}" not in combined.replace(" ", ""):
            return False, "requested_literal_not_realized"

    # Rename CONST from A to B: patch should contain both old and new evidence
    rename_m = re.search(
        r"\b(?:Rename|Change)\s+([A-Z][A-Z0-9_]*)\s+from\s+['\"]([^'\"]+)['\"]\s+to\s+['\"]([^'\"]+)['\"]",
        instruction,
        re.I,
    )
    if rename_m and action == "text_sub":
        old_val, new_val = rename_m.group(2), rename_m.group(3)
        old = str(patch.get("old", ""))
        new = str(patch.get("new", ""))
        if old_val not in old or new_val not in new:
            return False, "rename_missing_old_or_new_evidence"

    # Align docs/code: candidate must modify one side meaningfully
    if any(k in low for k in ("align", "agree", "match")) and any(
        k in low for k in ("docs", "version", "release", "spec", "endpoint")
    ):
        if action == "text_sub":
            old = str(patch.get("old", ""))
            new = str(patch.get("new", ""))
            if old == new or not new.strip():
                return False, "align_candidate_modifies_neither"

    return True, None


def _apply_semantic_ranking(instruction: str, candidates: list[PatchCandidate]) -> list[PatchCandidate]:
    """
    Stage 26: Compute semantic_match_score for each candidate.
    Higher score = better alignment with instruction (function name, return type, literal).
    """
    low = instruction.lower()
    for c in candidates:
        score = 0.0
        patch = c.patch
        action = patch.get("action")

        # Function name overlap with instruction
        if action == "insert" and patch.get("target_node") == "module_append":
            code = patch.get("code", "")
            def_m = re.search(r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code)
            if def_m:
                fname = def_m.group(1)
                if fname in instruction:
                    score += 1.0
                c.extra.setdefault("requested_symbol_name", fname)
        # text_sub for fix_return_value / return_binary_op_repair: symbol in instruction
        if action == "text_sub" and c.strategy in ("fix_return_value", "return_binary_op_repair"):
            for fname in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\)", instruction):
                if fname in ("fix", "make", "change", "return"):
                    continue
                if fname in instruction:
                    score += 0.5
                    c.extra.setdefault("requested_symbol_name", fname)
                    break

        # Return literal alignment
        ret_m = re.search(
            r"(?:return|returning)\s+(?:a\s+)?(?:non-empty\s+)?(?:string\s+)?(?:\(?\s*e\.g\.\s+)?['\"]([^'\"]+)['\"]",
            instruction,
            re.I,
        )
        if ret_m:
            requested = ret_m.group(1)
            c.extra.setdefault("requested_return_value", requested)
            code = patch.get("code", "") if action == "insert" else ""
            new = patch.get("new", "") if action == "text_sub" else ""
            if f'"{requested}"' in (code + new) or f"'{requested}'" in (code + new):
                score += 1.0

        # Severity/level-like words in instruction
        if any(w in low for w in ("severity", "level", "warn", "info", "debug", "error")):
            code = patch.get("code", "") if action == "insert" else ""
            if any(w in code.lower() for w in ("warn", "info", "debug", "error")):
                score += 0.5

        # Return type alignment (-> str, -> bool, -> int)
        if "-> str" in instruction or "-> str " in instruction:
            code = patch.get("code", "") if action == "insert" else ""
            if "-> str" in code:
                score += 0.5
        if "-> bool" in instruction:
            code = patch.get("code", "") if action == "insert" else ""
            if "-> bool" in code:
                score += 0.5
        if "-> int" in instruction:
            code = patch.get("code", "") if action == "insert" else ""
            if "-> int" in code:
                score += 0.5

        c.extra["semantic_match_score"] = min(score, 3.0)

    return candidates


def grounded_generation_telemetry(
    candidates: list[PatchCandidate],
    selected: PatchCandidate | None,
    rejected_reason: str | None = None,
) -> dict[str, Any]:
    """Build machine-readable telemetry dict (JSON-safe, bounded)."""
    best = selected or (candidates[0] if candidates else None)
    out = {
        "grounded_candidate_count": len(candidates),
        "selected_candidate_rank": selected.rank if selected else -1,
        "patch_candidate_strategy": best.strategy if best else None,
        "patch_candidate_evidence_type": best.evidence_type if best else None,
        "patch_candidate_evidence_excerpt": (
            best.evidence_excerpt[:MAX_EVIDENCE_LEN] if best else None
        ),
        "generation_rejected_reason": rejected_reason,
    }
    # Stage 28: repair type for RCA (existing-function vs missing-function)
    if best:
        strat = best.strategy
        out["grounded_repair_type"] = (
            "existing_function_repair"
            if strat in ("fix_return_value", "return_binary_op_repair", "empty_check_negation")
            else "missing_function_add" if strat == "add_missing_function" else "docs_code_align" if "align" in strat else "other"
        )
    return out


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def _try_return_binary_op_repair(instruction: str, text: str) -> PatchCandidate | None:
    """
    Generic: fix wrong binary operator in a return statement.
    When instruction signals a correct operation (divide, multiply, add, subtract)
    but the function body has the wrong one.
    """
    low = instruction.lower()
    # (wrong_op_pattern, correct_op) pairs driven by instruction keywords
    op_pairs: list[tuple[str, str]] = []
    if any(k in low for k in ("divid", "division", "quotient", "halve", "half", "halves")):
        op_pairs.append((r"\*", "/"))
    # halve(n) equals 2: integer division (n//2), not float
    if any(k in low for k in ("halve", "half")) and "equals" in low:
        op_pairs.append((r"/", "//"))
    if any(k in low for k in ("product", "multiplied")):
        op_pairs.append((r"/", "*"))
    if any(k in low for k in ("negate", "subtract", "difference", "minus")):
        op_pairs.append((r"\+", "-"))
    if any(k in low for k in (" sum ", "total", " add to", "addition", "add_ints", "add ints")):
        op_pairs.append((r"-", "+"))
    # "add" in function name (add_ints) or "equals 5" / "equals N" when instruction implies sum
    if "add" in low and ("equals" in low or "==" in low):
        op_pairs.append((r"\*", "+"))

    for wrong_re, correct_op in op_pairs:
        # Match: return IDENT op IDENT (with optional spaces and trailing comment)
        m = re.search(
            rf"(return\s+[a-zA-Z_]\w*\s*){wrong_re}(\s*[a-zA-Z_]\w*\s*(?:#.*)?$)",
            text,
            re.MULTILINE,
        )
        if m:
            old = m.group(0)
            # Replace the operator only (first occurrence in the matched string)
            wrong_literal = re.sub(r"\\", "", wrong_re)  # un-escape for replacement
            new = old.replace(wrong_literal, correct_op, 1)
            if new != old:
                return PatchCandidate(
                    patch={"action": "text_sub", "old": old, "new": new},
                    strategy="return_binary_op_repair",
                    evidence_type="matched_return_op_line",
                    evidence_excerpt=old[:MAX_EVIDENCE_LEN],
                    rank=0,
                )
    return None


def _try_fix_return_value(instruction: str, text: str) -> PatchCandidate | None:
    """
    Generic: fix existing function so it returns a specific literal.
    "fix get_timeout so it returns 30", "make F return X".
    Only fires when the named function exists with a different return.
    """
    low = instruction.lower()
    if not any(k in low for k in ("fix", "make", "change")) or "return" not in low:
        return None

    # Extract: "fix FNAME() in path so it returns 30" or "fix FNAME so it returns X"
    fname_m = re.search(
        r"\b(?:fix|make|change)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\)\s+.*?returns?\s+([0-9]+|True|False|None)\b",
        instruction,
        re.I,
    )
    if not fname_m:
        fname_m = re.search(
            r"\b(?:fix|make|change)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\(\))?\s*(?:so\s+it\s+)?returns?\s+([0-9]+|True|False|None)\b",
            instruction,
            re.I,
        )
    if not fname_m:
        fname_m = re.search(
            r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*\)\s+returns?\s+([0-9]+|True|False|None)\b",
            instruction,
            re.I,
        )
    if not fname_m:
        return None
    fname = fname_m.group(1)
    target_val = fname_m.group(2)

    # Function must exist
    if not re.search(rf"\bdef\s+{re.escape(fname)}\s*\(", text):
        return None

    # Find return line in target function (line-by-line after def fname)
    lines = text.split("\n")
    in_target = False
    for line in lines:
        if re.search(rf"^\s*def\s+{re.escape(fname)}\s*\(", line):
            in_target = True
            continue
        if in_target and re.match(r"^\s*(?:def |class )", line):
            break
        if not in_target:
            continue
        # Match return <number> or return True/False/None
        ret_m = re.search(r"return\s+([0-9]+|True|False|None)\s*(?:#.*)?$", line.strip())
        if ret_m:
            current = ret_m.group(1)
            if current == target_val:
                return None
            old_line = line
            new_line = re.sub(r"return\s+" + re.escape(current) + r"\b", f"return {target_val}", line, count=1)
            if new_line == old_line:
                return None
            return PatchCandidate(
                patch={"action": "text_sub", "old": old_line, "new": new_line},
                strategy="fix_return_value",
                evidence_type="matched_return_line",
                evidence_excerpt=old_line[:MAX_EVIDENCE_LEN],
                rank=0,
            )
    return None


def _try_empty_check_negation(instruction: str, text: str) -> PatchCandidate | None:
    """
    Generic: fix inverted emptiness check.
    When instruction says returns True for non-empty and code has len(s) == 0.
    """
    low = instruction.lower()
    has_nonempty = "non-empty" in low or "nonempty" in low or ("non" in low and "empty" in low)
    if not has_nonempty:
        return None

    # Pattern: return len(VAR) == 0 (should be > 0)
    m = re.search(
        r"(return\s+len\s*\(\s*\w+\s*\)\s*)==\s*0(\s*(?:#.*)?$)",
        text,
        re.MULTILINE,
    )
    if m:
        old = m.group(0)
        new = re.sub(r"==\s*0", "> 0", old)
        if new != old:
            return PatchCandidate(
                patch={"action": "text_sub", "old": old, "new": new},
                strategy="empty_check_negation",
                evidence_type="matched_inverted_empty_check",
                evidence_excerpt=old[:MAX_EVIDENCE_LEN],
                rank=0,
            )
    return None


def _try_raw_return_to_split(instruction: str, text: str) -> PatchCandidate | None:
    """
    Generic: replace plain `return VAR` with `return VAR.split()`.
    Fires when instruction says split on whitespace / return list of tokens
    and the function body returns a raw variable without .split().
    """
    low = instruction.lower()
    wants_split = (
        ("split" in low and "whitespace" in low)
        or "list of tokens" in low
        or ("split" in low and "token" in low)
    )
    if not wants_split:
        return None
    if ".split()" in text:
        return None  # already fixed

    # Match a plain bare `return VAR` line (no method call)
    m = re.search(
        r"(return\s+([a-zA-Z_]\w*))\s*(?:#.*)?$",
        text,
        re.MULTILINE,
    )
    if m and "." not in m.group(2):  # ensure VAR has no attribute access
        old = m.group(0).rstrip()
        var = m.group(2)
        new = f"return {var}.split()"
        return PatchCandidate(
            patch={"action": "text_sub", "old": old, "new": new},
            strategy="raw_return_to_split",
            evidence_type="matched_bare_return_line",
            evidence_excerpt=old[:MAX_EVIDENCE_LEN],
            rank=0,
        )
    return None


def _try_string_constant_rename(instruction: str, text: str) -> PatchCandidate | None:
    """
    Generic: rename a string constant when instruction says
    "Rename CONST_NAME from 'OLD' to 'NEW'" (case-insensitive, either quote style).
    """
    m = re.search(
        r"\b(?:Rename|Change|Update|Set)\s+([A-Z][A-Z0-9_]*)\s+from\s+['\"]([^'\"]+)['\"]\s+to\s+['\"]([^'\"]+)['\"]",
        instruction,
        re.I,
    )
    if not m:
        return None
    const_name, old_val, new_val = m.group(1), m.group(2), m.group(3)
    if const_name not in text:
        return None

    # Find the exact constant assignment line
    pm = re.search(
        rf"(^[ \t]*{re.escape(const_name)}\s*=\s*)([\"'])({re.escape(old_val)})\2([ \t]*)$",
        text,
        re.MULTILINE,
    )
    if not pm:
        return None
    pre, quote, _, trail = pm.group(1), pm.group(2), pm.group(3), pm.group(4)
    old_line = pm.group(0)
    new_line = f"{pre}{quote}{new_val}{quote}{trail}"
    if old_line == new_line:
        return None
    return PatchCandidate(
        patch={"action": "text_sub", "old": old_line, "new": new_line},
        strategy="string_constant_rename",
        evidence_type="matched_constant_assignment",
        evidence_excerpt=old_line[:MAX_EVIDENCE_LEN],
        rank=0,
    )


def _try_version_constant_align(
    instruction: str,
    file_path: str,
    file_content: str,
    project_root: str,
) -> PatchCandidate | None:
    """
    Generic: align a version constant in a .py file to the version in a paired .md file.
    Supports ## vX.Y.Z, **X.Y.Z**, version: X.Y.Z. Edits the .py constant to match the .md.
    """
    low = instruction.lower()
    if not ("align" in low or "agree" in low or "match" in low):
        return None
    if "version" not in low and "release" not in low and "changelog" not in low and "readme" not in low:
        return None

    p = Path(file_path)
    if p.suffix.lower() not in (".py", ".pyi"):
        return None

    # Find uppercase constant = "semver-like" (RELEASE_VERSION, TYPER_BENCH_VER, etc.)
    const_m = re.search(
        r"^[ \t]*([A-Z][A-Z0-9_]*)\s*=\s*\"([0-9]+\.[0-9]+(?:\.[0-9]+)?)\"\s*(?:#.*)?$",
        file_content,
        re.MULTILINE,
    )
    if not const_m:
        return None
    const_name = const_m.group(1)
    current_ver = const_m.group(2)
    old_line = const_m.group(0)

    # Use extended version finder (## vX.Y.Z, **X.Y.Z**, version: X.Y.Z)
    target_ver = _find_md_version_any_format(project_root, file_path, instruction)
    if not target_ver:
        target_ver = _find_md_version_header(project_root)
    if not target_ver or target_ver == current_ver:
        return None

    new_line = old_line.replace(f'"{current_ver}"', f'"{target_ver}"', 1)
    if new_line == old_line:
        return None

    return PatchCandidate(
        patch={"action": "text_sub", "old": old_line, "new": new_line},
        strategy="version_constant_align",
        evidence_type="matched_version_constant_and_md_header",
        evidence_excerpt=f"{const_name}={current_ver} -> {target_ver}"[:MAX_EVIDENCE_LEN],
        rank=1,
    )


def _try_url_constant_align(
    instruction: str,
    file_path: str,
    file_content: str,
    project_root: str,
) -> PatchCandidate | None:
    """
    Generic: align a URL constant in a .py file to the bold URL in a paired .md file.
    Works for any uppercase constant holding an http(s) URL paired with any .md file
    containing a **bold URL**. Edits the .py constant to match the .md URL.
    """
    low = instruction.lower()
    if not ("align" in low or "agree" in low or "match" in low):
        return None
    if not any(k in low for k in ("endpoint", "url", "spec", "service", "api")):
        return None

    p = Path(file_path)
    if p.suffix.lower() not in (".py", ".pyi"):
        return None

    # Find any uppercase constant = "http(s)://..." in file content
    const_m = re.search(
        r'^[ \t]*([A-Z][A-Z0-9_]*)\s*=\s*"(https?://[^"]+)"\s*(?:#.*)?$',
        file_content,
        re.MULTILINE,
    )
    if not const_m:
        return None
    const_name = const_m.group(1)
    current_url = const_m.group(2)
    old_line = const_m.group(0)

    # Scan project root for .md files with **URL** bold pattern
    md_url = _find_md_bold_url(project_root)
    if not md_url:
        return None

    try:
        md_netloc = urlparse(md_url).netloc
        py_netloc = urlparse(current_url).netloc
    except Exception:
        return None

    if md_netloc == py_netloc:
        return None  # already aligned

    new_line = old_line.replace(f'"{current_url}"', f'"{md_url}"', 1)
    if new_line == old_line:
        return None

    return PatchCandidate(
        patch={"action": "text_sub", "old": old_line, "new": new_line},
        strategy="url_constant_align",
        evidence_type="matched_url_constant_and_md_bold_url",
        evidence_excerpt=f"{const_name}: {py_netloc} -> {md_netloc}"[:MAX_EVIDENCE_LEN],
        rank=1,
    )


def _try_add_missing_function(
    instruction: str,
    file_content: str,
    suffix: str,
) -> PatchCandidate | None:
    """
    Generic: append a missing function when instruction explicitly names it with signature.
    Parses "Add FNAME() -> TYPE ... returning VALUE" or similar patterns.
    Only fires when the named function is confirmed absent from the file.
    """
    if suffix not in (".py", ".pyi"):
        return None

    # Extract: Add FNAME() -> TYPE
    sig_m = re.search(
        r"\bAdd\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*\)\s*->\s*([a-zA-Z_]\w*)",
        instruction,
        re.I,
    )
    if not sig_m:
        return None
    fname = sig_m.group(1)
    rtype = sig_m.group(2)

    # Confirm absence in file
    if re.search(rf"\bdef\s+{re.escape(fname)}\s*\(", file_content):
        return None  # already defined

    rval = _extract_return_value(instruction, rtype)
    code = f"\ndef {fname}() -> {rtype}:\n    return {rval}\n"

    return PatchCandidate(
        patch={
            "symbol": "",
            "action": "insert",
            "target_node": "module_append",
            "code": code,
        },
        strategy="add_missing_function",
        evidence_type="confirmed_function_absence",
        evidence_excerpt=f"{fname} not found in file; instruction: Add {fname}() -> {rtype}"[:MAX_EVIDENCE_LEN],
        rank=2,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_return_value(instruction: str, rtype: str) -> str:
    """Extract the concrete return value from the instruction text."""
    # "returning 3" or "returning False" etc.
    m = re.search(r"\breturning\s+(False|True|None)\b", instruction)
    if m:
        return m.group(1)
    m = re.search(r"\breturning\s+([0-9]+)\b", instruction)
    if m:
        return m.group(1)
    # "returning 'INFO'" or "returning \"WARN\""
    m = re.search(r"\breturning\s+['\"]([^'\"]+)['\"]", instruction)
    if m:
        val = m.group(1)
        return f'"{val}"'
    # "returns False by default" or "returns False"
    m = re.search(r"\breturns?\s+(False|True|None)\b", instruction)
    if m:
        return m.group(1)
    # "returns False by default"
    m = re.search(r"\breturns?\s+(False|True|None)\s+by\s+default", instruction)
    if m:
        return m.group(1)
    # "e.g. 'WARN'" or 'e.g. "INFO"'
    m = re.search(r"e\.g\.\s+['\"]([^'\"]+)['\"]", instruction)
    if m:
        val = m.group(1)
        return f'"{val}"'
    # Default by return type
    defaults = {"bool": "False", "int": "0", "float": "0.0", "str": '""', "list": "[]", "dict": "{}"}
    return defaults.get(rtype.lower(), "None")


def _find_md_version_header(project_root: str) -> str | None:
    """
    Scan project root (shallow) for .md files containing ## vX.Y.Z version header.
    Returns the version string (e.g. '1.5.0') from the first match, or None.
    """
    root = Path(project_root)
    for md_file in sorted(root.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"##\s+v([0-9]+\.[0-9]+(?:\.[0-9]+)?)", content)
        if m:
            return m.group(1)
    return None


def _find_md_version_any_format(project_root: str, file_path: str, instruction: str) -> str | None:
    """
    Find version from .md files. Supports ## vX.Y.Z, **X.Y.Z**, labeled versions.
    Looks in project root and same dir as file_path.
    """
    root = Path(project_root)
    search_dirs: list[Path] = [root]
    fp = Path(file_path)
    if fp.parent != root:
        search_dirs.append(root / fp.parent)
    cand_names = ["README.md", "CHANGELOG.md"]
    for d in search_dirs:
        for name in cand_names:
            md_file = d / name
            if not md_file.is_file():
                continue
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # ## vX.Y.Z
            m = re.search(r"##\s+v([0-9]+\.[0-9]+(?:\.[0-9]+)?)", content)
            if m:
                return m.group(1)
            # **X.Y.Z** (bold)
            m = re.search(r"\*\*([0-9]+\.[0-9]+(?:\.[0-9]+)?)\*\*", content)
            if m:
                return m.group(1)
            # version: X.Y.Z or Version: X.Y.Z
            m = re.search(r"[Vv]ersion[:\s]+\*\*?([0-9]+\.[0-9]+(?:\.[0-9]+)?)", content)
            if m:
                return m.group(1)
    return None


def _find_md_bold_url(project_root: str) -> str | None:
    """
    Scan project root (shallow) for .md files containing **URL** bold pattern.
    Returns the URL string from the first match, or None.
    """
    root = Path(project_root)
    for md_file in sorted(root.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"\*\*([^*]+)\*\*", content)
        if m:
            candidate = m.group(1).strip()
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None
