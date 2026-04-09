from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer


def test_coerce_modern_uses_required_symbols_when_present():
    parsed = {
        "relevance": "high",
        "confidence": "high",
        "understanding": "partial",
        "gaps": ["Need Foo.run implementation"],
        "required_symbols": ["Foo.run", "Bar.handler"],
        "is_sufficient": False,
    }
    out = UnderstandingAnalyzer._coerce_understanding(parsed)
    assert out.required_symbols == ["Foo.run", "Bar.handler"]


def test_coerce_modern_fallback_extracts_required_symbols_from_gaps():
    parsed = {
        "relevance": "medium",
        "confidence": "medium",
        "understanding": "partial",
        "gaps": ["Need ServiceA.execute flow and RepoClient.fetch details"],
        "required_symbols": [],
        "is_sufficient": False,
    }
    out = UnderstandingAnalyzer._coerce_understanding(parsed)
    assert "ServiceA.execute" in out.required_symbols
    assert "RepoClient.fetch" in out.required_symbols
