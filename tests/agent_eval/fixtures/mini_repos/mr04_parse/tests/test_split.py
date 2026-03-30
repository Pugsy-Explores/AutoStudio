from parse.split import tokenize


def test_tokenize_words():
    assert tokenize("a b c") == ["a", "b", "c"]
