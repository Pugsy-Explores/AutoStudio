# Architecture

Layers (bottom to top):

1. **config** — `settings.load()` produces immutable `Settings`.
2. **engine** — `engine.run(settings, event)` is the only entry; it validates then dispatches.
3. **dispatch** — maps event kinds to handler callables registered at import time.

Data flow: config file → `settings.load()` → `Settings` → `engine.run` reads `settings.timeout` and passes payload into dispatch.
