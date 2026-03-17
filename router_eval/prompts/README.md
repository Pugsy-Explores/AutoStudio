# Router Evaluation Prompts (`router_eval/prompts/`)

Prompt assets used by the router evaluation harness. This package exists to keep evaluation prompts versioned and separable from production prompt wiring.

## Responsibilities

- Store prompt templates or helper functions used by router variants under evaluation.
- Support swapping router implementations without changing the dataset.

## Invariants

- Evaluation hygiene: avoid changing dataset and routers/prompts in the same change when measuring improvements.

