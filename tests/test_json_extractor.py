import pytest

from agent_v2.utils.json_extractor import JSONExtractor


def test_extract_final_json_reasoning_then_json():
    text = "Thinking...\nstep by step\n{\"a\": 1, \"b\": \"ok\"}\n"
    out = JSONExtractor.extract_final_json(text)
    assert out == {"a": 1, "b": "ok"}


def test_extract_final_json_multiple_blocks_last_wins():
    text = '{"old": true}\nnoise\n{"new": 2}\n'
    out = JSONExtractor.extract_final_json(text)
    assert out == {"new": 2}


def test_extract_final_json_ignores_malformed_intermediate():
    text = '{"bad": }\nnotes\n{"good": 1}\n'
    out = JSONExtractor.extract_final_json(text)
    assert out == {"good": 1}


def test_extract_final_json_handles_nested_braces_inside_string():
    text = 'reasoning {"msg":"x { y } z","n":1} trailing'
    out = JSONExtractor.extract_final_json(text)
    assert out == {"msg": "x { y } z", "n": 1}


def test_extract_all_json_candidates_only_dicts():
    text = '{"a":1}\n[1,2,3]\n{"b":2}\n'
    out = JSONExtractor.extract_all_json_candidates(text)
    assert out == [{"a": 1}, {"b": 2}]


def test_extract_final_json_with_validate_fn():
    text = '{"kind":"draft"}\n{"kind":"final","x":1}\n'
    out = JSONExtractor.extract_final_json(text, validate_fn=lambda d: d.get("kind") == "final")
    assert out == {"kind": "final", "x": 1}


def test_extract_final_json_raises_when_no_valid_object():
    with pytest.raises(ValueError, match="No valid JSON object found"):
        JSONExtractor.extract_final_json("no json here")
