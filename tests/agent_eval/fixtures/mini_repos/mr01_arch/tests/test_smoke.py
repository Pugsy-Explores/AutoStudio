def test_import():
    from flowkit.engine import run

    assert run("ping") == "ok:ping"
