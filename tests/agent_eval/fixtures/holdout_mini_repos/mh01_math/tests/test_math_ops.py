from math_utils.ops import safe_div


def test_safe_div():
    assert safe_div(10, 2) == 5.0
