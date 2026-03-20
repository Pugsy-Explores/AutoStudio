from logging_utils.core import log_level


def test_log_level_returns_string():
    assert isinstance(log_level(), str)
    assert len(log_level()) > 0
