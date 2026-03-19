# Architecture

## Layers

1. **Config** (`config.py`) — loads defaults and timeouts for the pipeline.
2. **Pipeline** (`pipeline.py`) — `run()` orchestrates `process()` then `transform()` on input data.
3. **Constants** (`constants.py`) — shared names such as `APP_CONSTANT_NAME` used across modules.

Data flows: **config → pipeline.run → process/transform**. The public entry in `__init__.py` re-exports `run` for callers.

When renaming `APP_CONSTANT_NAME`, update both `constants.py` and any references in `__init__.py` exports documentation.
