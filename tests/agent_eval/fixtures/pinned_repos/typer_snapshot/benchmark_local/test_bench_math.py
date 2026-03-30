from benchmark_local.bench_math import double, halve


def test_double():
    assert double(3) == 6


def test_halve():
    assert halve(4) == 2
