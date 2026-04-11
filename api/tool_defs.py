"""Dynamic tool definitions for Gemma 4 E2B tool calling.

Builds OpenAI-compatible tool schema from register metadata.
A single ``query`` tool with a ``mode`` enum replaces the previous
7 separate tools.  The model picks the mode and fills parameters in one call.

Parameter names use Latin identifiers (Gemma works better with them),
while enum values and descriptions stay in Russian to match the data.
"""


def _filter_properties(register_metadata: dict) -> tuple[dict, list[str]]:
    """Build JSON Schema properties for filter dimensions.

    Returns (properties_dict, required_keys).
    Skips dimensions marked as technical in metadata.
    Falls back to hardcoded list if annotations are missing (backwards compat).
    """
    _FALLBACK_TECHNICAL = {"Показатель_номер", "Ед_изм", "Масштаб", "Месяц", "ПризнакДоход"}

    props = {}
    required: list[str] = []

    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        filter_type = dim.get("filter_type", "=")

        # Skip date dimensions — handled by year/month params
        if filter_type in ("year_month", "range"):
            continue

        # Skip technical dimensions
        if "technical" in dim:
            if dim["technical"]:
                continue
        elif name in _FALLBACK_TECHNICAL:
            continue

        key = _dim_key(name)
        allowed = dim.get("allowed_values", [])
        default = dim.get("default_value")

        prop: dict = {"type": "string"}

        # Use description_en from metadata if available
        if dim.get("description_en"):
            desc = dim["description_en"]
        else:
            desc = f"Dimension '{name}'"
            if dim.get("description"):
                desc += f". {dim['description']}"
        if default:
            desc += f". Default: {default}"

        if allowed:
            prop["enum"] = [str(v) for v in allowed]

        prop["description"] = desc
        props[key] = prop

    return props, required


def _dim_key(name: str) -> str:
    """Transliterate dimension name to a Latin key for JSON Schema."""
    mapping = {
        "Сценарий": "scenario",
        "КонтурПоказателя": "contour",
        "Показатель": "metric",
        "ДЗО": "company",
        "Масштаб": "scale",
        "Подразделение": "department",
        "ПризнакДоход": "income_flag",
        "Ед_изм": "unit",
        "Месяц": "period_month",
        "Показатель_номер": "metric_number",
    }
    return mapping.get(name, name)


# Reverse mapping for normalization back to 1C names
_KEY_TO_DIM: dict[str, str] = {
    "scenario": "Сценарий",
    "contour": "КонтурПоказателя",
    "metric": "Показатель",
    "company": "ДЗО",
    "scale": "Масштаб",
    "income_flag": "ПризнакДоход",
    "unit": "Ед_изм",
    "period_month": "Месяц",
    "metric_number": "Показатель_номер",
    "department": "Подразделение",
}


def key_to_dim(key: str) -> str:
    """Convert Latin key back to original dimension name."""
    return _KEY_TO_DIM.get(key, key)


def _resource_enum(register_metadata: dict) -> list[str]:
    """List of available resource names."""
    return [r["name"] for r in register_metadata.get("resources", [])] or ["Сумма"]


def _groupable_dimensions(register_metadata: dict) -> list[str]:
    """Latin keys for dimensions that can be used for GROUP BY.

    Uses role annotation from metadata. Falls back to hardcoded skip list.
    """
    _FALLBACK_SKIP_NAMES = {"Масштаб", "Ед_изм", "Показатель_номер", "Месяц", "ПризнакДоход"}

    result = []
    for d in register_metadata.get("dimensions", []):
        name = d["name"]

        # Skip date dimensions
        if d.get("data_type") == "Дата" or d.get("filter_type") in ("year_month", "range"):
            continue

        # Check annotations if available
        if "role" in d:
            if d.get("technical"):
                continue
            if d["role"] in ("group_by", "both"):
                result.append(_dim_key(name))
        else:
            # Fallback: old hardcoded logic
            if name not in _FALLBACK_SKIP_NAMES:
                result.append(_dim_key(name))

    return result


def build_tools(register_metadata: dict) -> list[dict]:
    """Build a single ``query`` tool definition from register metadata."""
    filter_props, _filter_required = _filter_properties(register_metadata)
    resources = _resource_enum(register_metadata)
    groupable = _groupable_dimensions(register_metadata)

    properties: dict = {
        "mode": {
            "type": "string",
            "enum": ["aggregate", "group_by", "compare"],
            "description": (
                "Query mode. "
                "aggregate = single number for a period ('какая выручка', 'сколько', 'итого'). "
                "group_by = breakdown by a dimension ('по ДЗО', 'в разрезе', 'топ-5'). "
                "compare = compare two values side by side ('факт vs план', 'сравни')."
            ),
        },
        "resource": {
            "type": "string",
            "enum": resources,
            "description": "Which resource to aggregate (e.g. Сумма)",
        },
        **filter_props,
        "year": {
            "type": "integer",
            "description": "Year from the question (e.g. 2025)",
        },
        "month": {
            "type": "integer",
            "description": "Month from the question (1-12). E.g. 'март' = 3",
        },
        "group_by": {
            "type": "string",
            "enum": groupable,
            "description": (
                "Dimension to group by (for group_by mode). "
                "Use for: 'по ДЗО' -> company, 'по показателям' -> metric, "
                "'по сценариям' -> scenario, 'в разрезе' -> pick dimension"
            ),
        },
        "compare_by": {
            "type": "string",
            "enum": groupable,
            "description": (
                "Dimension to compare across (for compare mode). "
                "'факт vs план' -> scenario, 'ДЗО-1 vs ДЗО-2' -> company"
            ),
        },
        "compare_values": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Exactly 2 values to compare (for compare mode). "
                "E.g. ['Факт', 'План'] or ['ДЗО-1', 'ДЗО-2']"
            ),
        },
    }

    required = ["mode", "resource", "year", "month"]

    tool_query = {
        "type": "function",
        "function": {
            "name": "query",
            "description": (
                "Query 1C analytics register data. "
                "Pick the right mode, fill resource, filters, and period."
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }

    return [tool_query]


def build_system_message(register_metadata: dict) -> str:
    """Build system message with few-shot examples for the query tool."""
    name = register_metadata.get("name", "")
    desc = register_metadata.get("description", "")

    # Collect dimension info for the prompt
    dim_lines: list[str] = []
    for dim in register_metadata.get("dimensions", []):
        dim_name = dim["name"]
        if dim.get("filter_type") in ("year_month", "range"):
            continue
        if dim.get("technical"):
            continue
        key = _dim_key(dim_name)
        allowed = dim.get("allowed_values", [])
        default = dim.get("default_value")
        parts = [f"  - {key} ({dim_name})"]
        if allowed:
            parts.append(f": {', '.join(str(v) for v in allowed)}")
        if default:
            parts.append(f" [default: {default}]")
        dim_lines.append("".join(parts))

    resources = _resource_enum(register_metadata)
    groupable = _groupable_dimensions(register_metadata)

    lines = [
        f"You are an analytics assistant for register {name}.",
    ]
    if desc:
        lines.append(f"Description: {desc}")

    lines.append("")
    lines.append("Available dimensions:")
    lines.extend(dim_lines)
    lines.append(f"Resources: {', '.join(resources)}")
    lines.append(f"Groupable dimensions: {', '.join(groupable)}")

    lines.append("")
    lines.append("MODES:")
    lines.append("- aggregate: single aggregated number for a period")
    lines.append("- group_by: breakdown by a dimension (also for top-N)")
    lines.append("- compare: compare two values of one dimension side by side")

    lines.append("")
    lines.append("EXAMPLES:")
    lines.append('Q: "Какая выручка за март 2025?"')
    lines.append('A: query(mode="aggregate", resource="Сумма", metric="Выручка", year=2025, month=3)')
    lines.append("")
    lines.append('Q: "Выручка по ДЗО за март 2025"')
    lines.append('A: query(mode="group_by", resource="Сумма", metric="Выручка", group_by="company", year=2025, month=3)')
    lines.append("")
    lines.append('Q: "Топ-5 ДЗО по выручке за март 2025"')
    lines.append('A: query(mode="group_by", resource="Сумма", metric="Выручка", group_by="company", year=2025, month=3)')
    lines.append("")
    lines.append('Q: "Сравни факт и план по выручке за март 2025"')
    lines.append('A: query(mode="compare", resource="Сумма", metric="Выручка", compare_by="scenario", compare_values=["Факт", "План"], year=2025, month=3)')
    lines.append("")
    lines.append('Q: "EBITDA за январь 2025"')
    lines.append('A: query(mode="aggregate", resource="Сумма", metric="EBITDA", year=2025, month=1)')

    lines.append("")
    lines.append("RULES:")
    lines.append("1. ALWAYS call the query tool. NEVER respond with plain text.")
    lines.append("2. Pick values STRICTLY from the allowed enums.")
    lines.append("3. If a filter value is not mentioned, use its default (Python applies defaults automatically).")
    lines.append("4. Extract year and month from Russian text: 'март 2025' -> year=2025, month=3.")
    lines.append("5. For top-N questions use group_by mode (Python handles limit).")

    return "\n".join(lines)
