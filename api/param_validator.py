"""Fast JSON parameter validation before sending to 1C HTTP service.

Catches obvious errors (wrong resource, invalid year/month, bad operator)
without making a network call. 1C does its own deeper validation.

Also performs best-effort normalization of enum values produced by small
models: case-insensitive and whitespace/punctuation-tolerant matching.
If a value maps unambiguously to one canonical string from the register's
enum, it is rewritten in-place. Only genuine mismatches surface as errors.
"""

import unicodedata
from dataclasses import dataclass, field

from .filter_utils import as_string_list

VALID_TOOLS = {"aggregate", "group_by", "compare"}
YEAR_MIN = 2020
YEAR_MAX = 2030


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def _norm(s) -> str:
    """Normalize for fuzzy comparison: strip, lowercase, drop punctuation, collapse spaces."""
    s = str(s).strip().lower()
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("P"))
    return " ".join(s.split())


def _resolve_enum(value, allowed: list[str]) -> tuple[str | None, list[str]]:
    """Map value to a canonical allowed entry tolerating case/spacing/punctuation.

    Returns (canonical, []) on unique match.
    Returns (None, candidates) when ambiguous or no match (candidates may be empty).
    """
    if not allowed:
        return str(value), []
    if value in allowed:
        return value, []

    v_norm = _norm(value)
    # Case/punctuation-insensitive exact
    for a in allowed:
        if _norm(a) == v_norm:
            return a, []

    # Substring match, symmetric
    subs: list[str] = []
    for a in allowed:
        a_norm = _norm(a)
        if a_norm and (v_norm in a_norm or a_norm in v_norm):
            subs.append(a)
    if len(subs) == 1:
        return subs[0], []
    if len(subs) > 1:
        return None, subs[:5]
    return None, []


def validate(tool_result: dict, register_metadata: dict) -> ValidationResult:
    """Validate tool_caller output before sending to 1C.

    Mutates tool_result["params"] to rewrite enum values to canonical form
    when an unambiguous match is found (e.g. "выручка" -> "Выручка").

    Args:
        tool_result: {"tool": str, "params": dict} from tool_caller
        register_metadata: register metadata with dimensions/resources

    Returns:
        ValidationResult with ok=True if valid, or list of error strings
        (English imperative wording so SLMs can self-correct on retry).
    """
    errors: list[str] = []

    tool = tool_result.get("tool")
    if not tool:
        return ValidationResult(ok=False, errors=["Модель не вызвала инструмент"])

    if tool not in VALID_TOOLS:
        errors.append(
            f'tool: copy EXACTLY one of {sorted(VALID_TOOLS)}. You wrote "{tool}".'
        )

    params = tool_result.get("params", {})
    if not params:
        return ValidationResult(ok=False, errors=["Пустые параметры"])

    # Resource check (with fuzzy resolve)
    resource = params.get("resource")
    valid_resources = [r["name"] for r in register_metadata.get("resources", [])]
    if resource and valid_resources:
        canonical, candidates = _resolve_enum(resource, valid_resources)
        if canonical is not None and canonical != resource:
            params["resource"] = canonical
        elif canonical is None:
            hint = candidates or valid_resources
            errors.append(
                f'resource: copy EXACTLY one of {hint}. You wrote "{resource}".'
            )

    # Period check
    period = params.get("period", {})
    year = period.get("year")
    month = period.get("month")
    if year is not None and not (YEAR_MIN <= year <= YEAR_MAX):
        errors.append(f"year: must be an integer between {YEAR_MIN} and {YEAR_MAX}, not {year}.")
    if month is not None and not (1 <= month <= 12):
        errors.append(f"month: must be an integer between 1 and 12, not {month}.")

    # Filter values check — each value is expected as a list of strings.
    # Scalars are tolerated (wrapped into a one-element list) and rewritten
    # as a list so downstream consumers see one consistent shape.
    filters = params.get("filters", {})
    dims_by_name = {d["name"]: d for d in register_metadata.get("dimensions", [])}
    for dim_name, value in list(filters.items()):
        if value is None:
            continue
        dim = dims_by_name.get(dim_name)
        if not dim:
            continue
        allowed = dim.get("allowed_values") or []

        items = as_string_list(value)
        if not allowed:
            # Nothing to resolve, but still normalize shape to a list.
            filters[dim_name] = items
            continue

        resolved: list[str] = []
        had_error = False
        for item in items:
            canonical, candidates = _resolve_enum(item, allowed)
            if canonical is not None:
                resolved.append(canonical)
            else:
                hint = candidates or allowed
                errors.append(
                    f'{dim_name}: copy EXACTLY one of {hint}. '
                    f'You wrote "{item}".'
                )
                had_error = True

        if not had_error:
            filters[dim_name] = resolved

    # compare_values: resolve each element against compare_by's allowed values
    if tool == "compare":
        values = params.get("values", [])
        if not isinstance(values, list) or len(values) != 2:
            errors.append("compare requires values — an array of exactly 2 elements.")
        else:
            compare_by = params.get("compare_by")
            dim = dims_by_name.get(compare_by) if compare_by else None
            allowed = dim.get("allowed_values") or [] if dim else []
            if allowed:
                for i, v in enumerate(values):
                    canonical, candidates = _resolve_enum(v, allowed)
                    if canonical is not None and canonical != v:
                        values[i] = canonical
                    elif canonical is None:
                        hint = candidates or allowed
                        errors.append(
                            f'compare_values[{i}]: copy EXACTLY one of {hint}. '
                            f'You wrote "{v}".'
                        )

    if tool == "group_by":
        group_by = params.get("group_by", [])
        if not group_by:
            errors.append("group_by mode requires a non-empty group_by parameter.")

    return ValidationResult(ok=len(errors) == 0, errors=errors)
