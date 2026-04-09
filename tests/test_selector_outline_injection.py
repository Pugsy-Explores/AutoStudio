"""Tests for bounded selector outline injection (sort + full vs signature + trim marker)."""

from agent_v2.exploration.llm_input_normalize import normalize_selector_batch
from agent_v2.exploration.selector_outline_injection import (
    SELECTOR_CODE_TRIM_MARKER,
    TRIMMED_LINE_PREFIX,
    _mark_signature_trimmed,
    _signature_for_row,
    _total_injected_code_len_for_k,
    prepare_outline_for_selector_prompt,
    sort_outline_rows_for_selector,
    total_selector_outline_code_chars,
)


def _row(name: str, typ: str, code: str) -> dict[str, str]:
    return {"name": name, "type": typ, "start_line": "1", "end_line": "9", "code": code}


def test_below_threshold_all_full_code():
    rows = [
        _row("zebra", "function", "def zebra():\n    return 2\n"),
        _row("alpha", "function", "def alpha():\n    return 1\n"),
    ]
    out = prepare_outline_for_selector_prompt(rows, max_code_chars=100_000)
    assert len(out) == 2
    assert out[0]["name"] == "alpha"
    assert out[1]["name"] == "zebra"
    assert "return 1" in out[0]["code"]
    assert "return 2" in out[1]["code"]
    assert SELECTOR_CODE_TRIM_MARKER not in "\n".join(r["code"] for r in out)


def test_above_threshold_trims_with_marker_and_signatures_for_overflow():
    a = _row("a", "function", "def a():\n    pass\n")
    b = _row("b", "function", "def b():\n" + ("    y\n" * 500))
    rows = [b, a]
    sorted_rows = sort_outline_rows_for_selector(rows)
    n = len(sorted_rows)
    full_lens = [len(str(r.get("code") or "")) for r in sorted_rows]
    sig_lens = [len(_mark_signature_trimmed(_signature_for_row(r, sorted_rows))) for r in sorted_rows]
    max_c = _total_injected_code_len_for_k(1, n=n, full_lens=full_lens, sig_lens=sig_lens)
    out = prepare_outline_for_selector_prompt(rows, max_code_chars=max_c)
    joined = "\n".join(r["code"] for r in out)
    assert SELECTOR_CODE_TRIM_MARKER in joined
    assert out[0]["name"] == "a" and "pass" in out[0]["code"]
    assert out[1]["name"] == "b" and "y\n" not in out[1]["code"]
    assert f"{TRIMMED_LINE_PREFIX}def b():" in out[1]["code"]
    assert TRIMMED_LINE_PREFIX not in out[0]["code"]
    assert total_selector_outline_code_chars(out) <= max_c


def test_trimmed_overflow_is_signatures_only_not_full_bodies():
    long_body = "def mid():\n" + "\n".join(f"    line_{i}()" for i in range(500))
    rows = [
        _row("first", "function", "def first():\n    pass\n"),
        _row("second", "function", long_body),
        _row("third", "function", "def third():\n    pass\n"),
    ]
    sorted_rows = sort_outline_rows_for_selector(rows)
    n = len(sorted_rows)
    full_lens = [len(str(r.get("code") or "")) for r in sorted_rows]
    sig_lens = [len(_mark_signature_trimmed(_signature_for_row(r, sorted_rows))) for r in sorted_rows]
    max_c = _total_injected_code_len_for_k(1, n=n, full_lens=full_lens, sig_lens=sig_lens)
    out = prepare_outline_for_selector_prompt(rows, max_code_chars=max_c)
    assert SELECTOR_CODE_TRIM_MARKER in "\n".join(r["code"] for r in out)
    third = next(r for r in out if r["name"] == "third")
    assert "line_0()" not in third["code"]
    assert f"{TRIMMED_LINE_PREFIX}def third():" in third["code"]


def test_trimmed_prefix_positions_for_class_and_methods():
    rows = [
        _row("a", "function", "def a():\n    return 1\n"),
        _row("pkg.Widget", "class", "class Widget:\n" + ("    work()\n" * 200)),
        _row("pkg.Widget.build", "method", "def build(self, x):\n    return x\n"),
        _row("pkg.Widget.run", "method", "def run(self):\n    return 0\n"),
    ]
    sorted_rows = sort_outline_rows_for_selector(rows)
    n = len(sorted_rows)
    full_lens = [len(str(r.get("code") or "")) for r in sorted_rows]
    sig_lens = [len(_mark_signature_trimmed(_signature_for_row(r, sorted_rows))) for r in sorted_rows]
    max_c = _total_injected_code_len_for_k(1, n=n, full_lens=full_lens, sig_lens=sig_lens)
    out = prepare_outline_for_selector_prompt(rows, max_code_chars=max_c)

    # first symbol full body remains untouched
    assert TRIMMED_LINE_PREFIX not in out[0]["code"]
    # trimmed class header and each method line are prefixed
    cls = next(r for r in out if r["type"] == "class")
    assert "[trimmed] class" in cls["code"]
    assert "    [trimmed] def build(" in cls["code"]
    assert "    [trimmed] def run(" in cls["code"]


def test_trimmed_prefix_is_at_line_start_or_after_indent():
    rows = [
        _row("a", "function", "def a():\n    return 1\n"),
        _row("b", "function", "def b(x):\n    return x\n"),
    ]
    out = prepare_outline_for_selector_prompt(rows, max_code_chars=1)
    trimmed = out[1]["code"].splitlines()
    sig_lines = [ln for ln in trimmed if ln.strip() and "def " in ln]
    assert sig_lines
    assert all(ln.startswith("[trimmed] ") or ln.startswith("    [trimmed] ") for ln in sig_lines)


def test_ordering_is_deterministic_alphabetical():
    rows = [
        _row("m.a", "method", "    def a(self):\n        return 1\n"),
        _row("m", "class", "class m:\n    pass\n"),
        _row("z", "function", "def z():\n    return 3\n"),
    ]
    sorted_rows = sort_outline_rows_for_selector(rows)
    names = [r["name"] for r in sorted_rows]
    assert names == ["m", "m.a", "z"]


def test_total_length_respects_budget_when_feasible():
    rows = [
        _row("a", "function", "def a():\n    pass\n"),
        _row("b", "function", "def b():\n    pass\n"),
    ]
    max_c = 500
    out = prepare_outline_for_selector_prompt(rows, max_code_chars=max_c)
    assert total_selector_outline_code_chars(out) <= max_c


def test_normalize_selector_batch_includes_trim_marker_when_injected():
    from agent_v2.exploration.candidate_selector import _selector_candidate_payload
    from agent_v2.schemas.exploration import ExplorationCandidate

    long_body = "def big():\n" + ("    pass\n" * 8000)
    ol = prepare_outline_for_selector_prompt(
        [
            _row("big", "function", long_body),
            _row("small", "function", "def small():\n    return 0\n"),
        ],
        max_code_chars=200,
    )
    payload = _selector_candidate_payload(
        ExplorationCandidate(file_path="f.py", symbol="x", source="grep"),
        outline_for_prompt=ol,
    )
    text = normalize_selector_batch(
        instruction="i",
        intent="intent",
        limit=1,
        explored_block="",
        items=[payload],
    )
    assert SELECTOR_CODE_TRIM_MARKER in text

