from pkg_b.consumer import get_prefix


def test_prefix_is_new():
    assert "new" in get_prefix()
