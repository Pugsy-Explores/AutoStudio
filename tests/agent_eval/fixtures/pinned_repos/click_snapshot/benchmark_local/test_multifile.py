from benchmark_local.part_b import label


def test_suffix_coherent():
    assert "unified" in label()
