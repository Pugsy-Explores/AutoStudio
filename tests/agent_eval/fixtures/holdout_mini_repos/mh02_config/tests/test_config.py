from config.settings import enable_debug


def test_enable_debug_default_false():
    assert enable_debug() is False
