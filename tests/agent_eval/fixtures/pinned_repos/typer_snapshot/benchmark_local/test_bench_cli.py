from benchmark_local.bench_cli import describe_app


def test_describe_nonempty():
    assert len(describe_app().strip()) > 0
