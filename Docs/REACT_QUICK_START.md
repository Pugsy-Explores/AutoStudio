# ReAct Quick Start

ReAct mode is the default execution path. The model selects actions (search, open_file, edit, run_tests, finish) step-by-step.

---

## Prerequisites

- Python 3.10+
- OpenAI-compatible LLM endpoint (e.g. llama.cpp, vLLM, or OpenAI API)
- Optional: retrieval daemon for code search (auto-starts if configured)

---

## Run the Agent

```bash
# Single instruction (uses run_controller → ReAct loop)
python -m agent "Add a docstring to the main function in agent/__main__.py"

# Live mode — full trace output to Docs/react_runs/
python scripts/run_react_live.py "Add a docstring to the main function in agent/__main__.py"
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `REACT_MODE` | 1 (default) = ReAct; 0 = legacy deterministic |
| `REASONING_MODEL_ENDPOINT` | LLM endpoint (e.g. `http://localhost:8081/v1/chat/completions`) |
| `SERENA_PROJECT_DIR` | Project root (default: cwd) |

See [CONFIGURATION.md](CONFIGURATION.md) for model endpoints.

---

## Trace Output (run_react_live)

- **Location:** `Docs/react_runs/react_trace_{timestamp}.json`
- **Contents:** instruction, json_actions (thought, action, args per step), react_history_full, patches_applied, files_modified, errors_encountered

---

## See Also

- [REACT_ARCHITECTURE.md](REACT_ARCHITECTURE.md) — Full architecture
- [REACT_LIVE_EXECUTION_REPORT_20260323.md](REACT_LIVE_EXECUTION_REPORT_20260323.md) — Live run report
