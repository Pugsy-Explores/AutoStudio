## Quarantined Slow Tests

These tests are temporarily moved out of the default `tests/` sweep because
they repeatedly exceed per-file timeout in local/CI file-by-file runs.

Quarantined files:

- `agent_eval/test_benchmark_smoke.py`
- `agent_eval/test_stage14_benchmark_infra.py`
- `agent_eval/test_stage17_exec_hardening.py`
- `agent_eval/test_stage28_eval_integrity.py`
- `agent_eval/test_stage31_eval_matrix.py`
- `agent_eval/test_stage32_modularization.py`
- `agent_eval/test_stage33_paired_eval.py`
- `agent_eval/test_stage34_paired_real.py`

Run them explicitly when needed:

```bash
python3 -m pytest tests/quarantined_slow -q
```
