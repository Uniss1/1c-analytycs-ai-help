"""Shared helpers for filter-value handling across the tool pipeline."""


def as_string_list(value) -> list[str]:
    """Normalize a filter value to a list of strings.

    None → [].
    List → [str(x) for x in value if x is not None].
    Scalar → [str(value)] (empty string → []).

    Empty strings inside a list are preserved as-is (callers decide whether
    to treat an empty string as "absent" or a valid value).
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    s = str(value)
    return [s] if s else []
