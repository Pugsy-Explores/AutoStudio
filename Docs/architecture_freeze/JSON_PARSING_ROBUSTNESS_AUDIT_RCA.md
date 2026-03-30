# JSON Parsing Robustness Audit (Exploration Pipeline)

Date: 2026-03-26  
Owner: Exploration pipeline audit (senior engineering review)

## Scope

- `agent_v2/exploration/query_intent_parser.py`
- `agent_v2/exploration/exploration_scoper.py`
- `agent_v2/exploration/candidate_selector.py` (single + batch)
- `agent_v2/exploration/understanding_analyzer.py`

## Audit Goal

Verify parser behavior for:

- reasoning/thinking text before JSON
- multiple JSON-like blocks in one output
- malformed intermediate blocks
- deterministic final-block selection

Required policy:

- ignore non-JSON text
- parse all JSON object candidates safely
- return only the final valid JSON object
- fail loudly if no valid JSON object exists

## Findings (Pre-fix)

1. Parsers were fragmented (`_parse_json_object` duplicated in multiple files).
2. Regex/dumb `json.loads` paths were fragile with mixed text + JSON outputs.
3. No consistent "last valid JSON object wins" behavior existed across components.
4. Some components silently recovered with pass-through/heuristics, hiding parse failures.

## Implemented Remediation

Introduced shared extractor:

- `agent_v2/utils/json_extractor.py`
  - `JSONExtractor.extract_all_json_candidates(text: str) -> list[dict]`
  - `JSONExtractor.extract_final_json(text: str, validate_fn=None) -> dict`

Implementation properties:

- balanced-brace scanning (string + escape aware; not regex-only)
- `json.loads` attempted per candidate object slice
- only top-level dict results retained
- deterministic final valid dict returned
- strict failure when none found:

```python
ValueError(
  "No valid JSON object found (last-valid-json policy). "
  f"Candidates tried: {n}, tail: {text[-500:]}"
)
```

## Integration Changes

All exploration parsing call sites migrated to:

```python
JSONExtractor.extract_final_json(raw_response_text)
```

Updated files:

- `agent_v2/exploration/query_intent_parser.py`
- `agent_v2/exploration/exploration_scoper.py`
- `agent_v2/exploration/candidate_selector.py`
- `agent_v2/exploration/understanding_analyzer.py`

Legacy `_parse_json_object` implementations removed.

## Strict Mode Behavior

Removed parsing-related silent recovery paths:

- no parse-error pass-through in scoper
- no parse-error heuristic fallback ranking in selector
- no parse-error heuristic decision in analyzer

Outcome:

- parse/shape/schema issues now fail fast and surface immediately.

## Example Outputs Covered

1. reasoning + final JSON
2. multiple JSON blocks (last valid object selected)
3. malformed intermediate block + valid final block

## Notes

This work intentionally focuses on the parsing layer only and keeps schema contracts intact.
