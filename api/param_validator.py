"""Fast JSON parameter validation before sending to 1C HTTP service.

Catches obvious errors (wrong resource, invalid year/month, bad operator)
without making a network call. 1C does its own deeper validation.
"""

from dataclasses import dataclass, field

VALID_TOOLS = {"aggregate", "group_by", "compare"}
YEAR_MIN = 2020
YEAR_MAX = 2030


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate(tool_result: dict, register_metadata: dict) -> ValidationResult:
    """Validate tool_caller output before sending to 1C.

    Args:
        tool_result: {"tool": str, "params": dict} from tool_caller
        register_metadata: register metadata with dimensions/resources

    Returns:
        ValidationResult with ok=True if valid, or list of error strings.
    """
    errors = []

    tool = tool_result.get("tool")
    if not tool:
        return ValidationResult(ok=False, errors=["Модель не вызвала инструмент"])

    if tool not in VALID_TOOLS:
        errors.append(f"Неизвестный инструмент: {tool}")

    params = tool_result.get("params", {})
    if not params:
        return ValidationResult(ok=False, errors=["Пустые параметры"])

    # Resource check
    resource = params.get("resource")
    if resource:
        valid_resources = [r["name"] for r in register_metadata.get("resources", [])]
        if valid_resources and resource not in valid_resources:
            errors.append(f"Неизвестный resource '{resource}'. Допустимые: {valid_resources}")

    # Period check
    period = params.get("period", {})
    year = period.get("year")
    month = period.get("month")
    if year is not None and not (YEAR_MIN <= year <= YEAR_MAX):
        errors.append(f"Год {year} вне диапазона {YEAR_MIN}–{YEAR_MAX}")
    if month is not None and not (1 <= month <= 12):
        errors.append(f"Месяц {month} вне диапазона 1–12")

    # Filter values check
    filters = params.get("filters", {})
    dims_by_name = {d["name"]: d for d in register_metadata.get("dimensions", [])}
    for dim_name, value in filters.items():
        if value is None:
            continue
        dim = dims_by_name.get(dim_name)
        if not dim:
            continue
        allowed = dim.get("allowed_values") or []
        if allowed and value not in allowed:
            errors.append(f"{dim_name}: '{value}' не из допустимых {allowed}")

    # Tool-specific checks
    if tool == "compare":
        values = params.get("values", [])
        if not isinstance(values, list) or len(values) != 2:
            errors.append("compare требует values — массив из ровно 2 элементов")

    if tool == "group_by":
        group_by = params.get("group_by", [])
        if not group_by:
            errors.append("group_by требует непустой параметр group_by")

    return ValidationResult(ok=len(errors) == 0, errors=errors)
