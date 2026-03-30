from benchmark_local.bench_requests_meta import get_timeout


def test_get_timeout():
    assert get_timeout() == 30
