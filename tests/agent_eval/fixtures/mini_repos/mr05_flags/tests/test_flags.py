from flags.store import is_verbose


def test_verbose_default():
    assert is_verbose() is False
