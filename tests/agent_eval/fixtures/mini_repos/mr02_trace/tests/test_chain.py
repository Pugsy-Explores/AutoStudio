from chain.entry import run


def test_run_upper():
    assert run("ab") == "AB"
