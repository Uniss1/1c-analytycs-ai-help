from api.filter_utils import as_string_list


def test_none_returns_empty():
    assert as_string_list(None) == []


def test_empty_string_returns_empty():
    assert as_string_list("") == []


def test_scalar_string_wrapped():
    assert as_string_list("Факт") == ["Факт"]


def test_scalar_int_stringified():
    assert as_string_list(42) == ["42"]


def test_list_preserved():
    assert as_string_list(["Факт", "План"]) == ["Факт", "План"]


def test_list_drops_none_preserves_empty_strings():
    assert as_string_list([None, "a", ""]) == ["a", ""]


def test_list_stringifies_mixed_types():
    assert as_string_list([1, "two", 3.5]) == ["1", "two", "3.5"]
