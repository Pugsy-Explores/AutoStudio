# JSON Parsing Strict Implementation Prompt (Execution Record)

## Prompt Objective

Implement a robust, reusable JSON extraction utility and replace all fragile exploration parsers with deterministic last-valid JSON extraction.

## Required Implementation Summary

1. Create:
   - `agent_v2/utils/json_extractor.py`
   - `JSONExtractor.extract_final_json(text, validate_fn=None)`
   - `JSONExtractor.extract_all_json_candidates(text)`
2. Behavior:
   - ignore non-JSON text
   - extract all object candidates via balanced-brace scanning
   - parse each via `json.loads`
   - keep dict objects only
   - return final valid dict
3. Failure policy:
   - raise explicit `ValueError` if no valid object is found
4. Integrate into:
   - `query_intent_parser.py`
   - `exploration_scoper.py`
   - `candidate_selector.py`
   - `understanding_analyzer.py`
5. Remove local `_parse_json_object` implementations.
6. Strict mode:
   - remove parse-error/pass-through/heuristic recovery paths
   - fail fast on parse/shape/schema errors

## Implemented Outcome

Completed as requested:

- shared extractor implemented and wired into all exploration parsing call sites
- old parser helpers removed
- strict failure semantics enforced for parsing layer
- fallback parse recovery removed in scoped components

## Canonical Policy

`reasoning -> noise -> partial/malformed JSON -> FINAL valid JSON`  
=> final valid top-level JSON object is used.
