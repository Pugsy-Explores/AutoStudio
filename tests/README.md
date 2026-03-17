# Tests (`tests/`)

AutoStudio test suite.

## Responsibilities

- Unit and integration tests covering core subsystems (routing, planning, retrieval, editing/runtime safety, observability).
- Provide fixtures and small sample repos used by retrieval/indexing tests.

## Subpackages

- `integration/`: end-to-end wiring and service-boundary tests — see [`tests/integration/README.md`](integration/README.md)
- `fixtures/`: fixture repos and test data (not all fixture directories are Python packages).
  - `tests/fixtures/repo/sub/`: nested package fixture — see [`tests/fixtures/repo/sub/README.md`](fixtures/repo/sub/README.md)

