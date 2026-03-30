"""Token estimation helpers on the model client boundary."""

from agent.models.model_client import estimate_tokens


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_length() -> None:
    # 38 chars / 3.8 ≈ 10
    assert estimate_tokens("x" * 38) == 10


def test_estimate_tokens_fractional_truncates() -> None:
    assert estimate_tokens("abc") == 0  # len 3 / 3.8 < 1

