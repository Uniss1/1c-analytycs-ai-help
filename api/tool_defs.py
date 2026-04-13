"""Dynamic tool definitions for Gemma 4 E2B tool calling.

Builds OpenAI-compatible tool schema from register metadata.
A single ``query`` tool with a ``mode`` enum replaces the previous
7 separate tools.  The model picks the mode and fills parameters in one call.

Parameter names use Latin identifiers (Gemma works better with them),
while enum values and descriptions stay in Russian to match the data.
"""


_FALLBACK_TECHNICAL = {"Показатель_номер", "Ед_изм", "Масштаб", "Месяц", "ПризнакДоход"}


def is_technical_dim(dim: dict) -> bool:
    """True if the dimension should be hidden from the model and skipped in
    clarification/validation logic.

    Uses the explicit ``technical`` flag if set by sync_metadata interview,
    otherwise falls back to a hardcoded list for backwards compatibility with
    registers where annotations have never been generated.
    """
    if "technical" in dim:
        return bool(dim["technical"])
    return dim.get("name") in _FALLBACK_TECHNICAL


def _filter_properties(register_metadata: dict) -> tuple[dict, list[str]]:
    """Build JSON Schema properties for filter dimensions.

    Filter values are always arrays of strings. Small models sometimes emit
    scalars — tool_caller normalizes those to single-element arrays before
    validation.

    Returns (properties_dict, required_keys).
    Skips dimensions marked as technical in metadata.
    Falls back to hardcoded list if annotations are missing (backwards compat).
    """

    props = {}
    required: list[str] = []

    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        filter_type = dim.get("filter_type", "=")

        # Skip date dimensions — handled by year/month params
        if filter_type in ("year_month", "range"):
            continue

        # Skip technical dimensions
        if is_technical_dim(dim):
            continue

        key = _dim_key(name)
        allowed = dim.get("allowed_values", [])
        default = dim.get("default_value")

        item_schema: dict = {"type": "string"}
        if allowed:
            item_schema["enum"] = [str(v) for v in allowed]

        if dim.get("description_en"):
            desc = dim["description_en"]
        else:
            desc = f"Dimension '{name}'"
            if dim.get("description"):
                desc += f". {dim['description']}"
        desc = desc.rstrip(".") + ". Always pass as array, even for one value."
        if default:
            desc += f" Default: {default}."

        props[key] = {
            "type": "array",
            "items": item_schema,
            "description": desc,
        }

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

    Uses role annotation from metadata. Falls back to hardcoded skip list
    (via is_technical_dim).
    """
    result = []
    for d in register_metadata.get("dimensions", []):
        name = d["name"]

        # Skip date dimensions
        if d.get("data_type") == "Дата" or d.get("filter_type") in ("year_month", "range"):
            continue

        if is_technical_dim(d):
            continue

        # Respect explicit role annotation when present
        if d.get("role") is not None and d["role"] not in ("group_by", "both"):
            continue

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
            "description": (
                "Month 1-12 (e.g. 'март' = 3). "
                "Omit entirely for whole-year queries ('за 2024 год')."
            ),
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

    required = ["mode", "resource", "year"]

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


def _pick_example_dims(register_metadata: dict) -> dict:
    """Pick concrete dimensions/values from metadata for few-shot examples.

    Data-driven: reads actual allowed_values from the register so examples
    never disagree with the enum constraint in tool schema.

    Returns dict with keys: resource, metric_dim, metric_value, group_dim,
    compare_dim, compare_values. Missing items are None if the register
    lacks a suitable dimension.
    """
    result: dict = {
        "resource": None,
        "metric_dim": None,       # (name, key, value) for a filter-like dim
        "metric_value": None,
        "metric_key": None,
        "group_dim": None,        # (name, key) for a groupable dim
        "group_key": None,
        "compare_dim": None,
        "compare_key": None,
        "compare_values": None,
    }

    resources = register_metadata.get("resources", [])
    result["resource"] = resources[0]["name"] if resources else None

    filter_candidate = None  # "the thing we're asking about"
    group_candidate = None   # "the axis we're slicing along"
    compare_candidate = None # dim with >=2 allowed values

    for dim in register_metadata.get("dimensions", []):
        if is_technical_dim(dim):
            continue
        if dim.get("filter_type") in ("year_month", "range"):
            continue
        allowed = dim.get("allowed_values") or []
        if not allowed:
            continue
        role = dim.get("role")

        # Filter candidate: prefer role=filter, else role=both, else any
        if filter_candidate is None:
            filter_candidate = dim
        elif role == "filter" and filter_candidate.get("role") != "filter":
            filter_candidate = dim

        # Group candidate: prefer role=group_by/both, skip role=filter-only
        if role in ("group_by", "both") or role is None:
            if group_candidate is None:
                group_candidate = dim

        # Compare candidate: needs >=2 allowed values
        if len(allowed) >= 2 and (role in ("group_by", "both") or role is None):
            if compare_candidate is None:
                compare_candidate = dim

    # Ensure filter_dim and group_dim are different if possible
    if filter_candidate and group_candidate and filter_candidate is group_candidate:
        for dim in register_metadata.get("dimensions", []):
            if dim is filter_candidate or is_technical_dim(dim):
                continue
            if dim.get("filter_type") in ("year_month", "range"):
                continue
            if not (dim.get("allowed_values") or []):
                continue
            role = dim.get("role")
            if role in ("group_by", "both") or role is None:
                group_candidate = dim
                break

    if filter_candidate:
        name = filter_candidate["name"]
        result["metric_dim"] = name
        result["metric_key"] = _dim_key(name)
        result["metric_value"] = filter_candidate["allowed_values"][0]
    if group_candidate:
        name = group_candidate["name"]
        result["group_dim"] = name
        result["group_key"] = _dim_key(name)
    if compare_candidate:
        name = compare_candidate["name"]
        result["compare_dim"] = name
        result["compare_key"] = _dim_key(name)
        result["compare_values"] = list(compare_candidate["allowed_values"][:2])

    return result


def _format_kwargs(pairs: list[tuple[str, object]], filter_keys: set[str] | None = None) -> str:
    """Render list of (key, value) as Python-like kwargs for few-shot.

    filter_keys: keys that must be rendered as arrays; wraps scalars in [..].
    """
    filter_keys = filter_keys or set()
    out = []
    for k, v in pairs:
        if v is None:
            continue
        if isinstance(v, list):
            rendered = "[" + ", ".join(f'"{x}"' for x in v) + "]"
        elif k in filter_keys and isinstance(v, str):
            rendered = f'["{v}"]'
        elif isinstance(v, str):
            rendered = f'"{v}"'
        else:
            rendered = str(v)
        out.append(f"{k}={rendered}")
    return ", ".join(out)


def build_system_message(register_metadata: dict) -> str:
    """Build system message with data-driven few-shot examples for the query tool."""
    name = register_metadata.get("name", "")
    desc = register_metadata.get("description", "")

    # Collect dimension info for the prompt
    dim_lines: list[str] = []
    for dim in register_metadata.get("dimensions", []):
        dim_name = dim["name"]
        if dim.get("filter_type") in ("year_month", "range"):
            continue
        if is_technical_dim(dim):
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

    # Data-driven few-shot: use actual values from register metadata so the
    # model never sees examples that contradict the enum constraint.
    ex = _pick_example_dims(register_metadata)
    res = ex["resource"] or (resources[0] if resources else "Сумма")
    metric_value = ex["metric_value"]
    metric_key = ex["metric_key"]
    group_key = ex["group_key"]
    compare_key = ex["compare_key"]
    compare_values = ex["compare_values"]

    # Compute filter_keys: all dim keys that are filter-like (non-date, non-technical)
    filter_keys = {
        _dim_key(d["name"])
        for d in register_metadata.get("dimensions", [])
        if d.get("filter_type") not in ("year_month", "range")
        and not is_technical_dim(d)
    }

    lines.append("")
    lines.append("EXAMPLES (values taken from this register's enums — copy them exactly):")

    # aggregate
    agg_kwargs: list[tuple[str, object]] = [("mode", "aggregate"), ("resource", res)]
    if metric_key and metric_value:
        agg_kwargs.append((metric_key, metric_value))
    agg_kwargs += [("year", 2025), ("month", 3)]
    topic = metric_value or res
    lines.append(f'Q: "Какой показатель {topic} за март 2025?"')
    lines.append(f'A: query({_format_kwargs(agg_kwargs, filter_keys)})')

    # year-only example (no month) — teaches model to omit month for whole-year queries
    yo_kwargs: list[tuple[str, object]] = [("mode", "aggregate"), ("resource", res)]
    if metric_key and metric_value:
        yo_kwargs.append((metric_key, metric_value))
    yo_kwargs.append(("year", 2024))
    lines.append("")
    lines.append(f'Q: "{topic} за 2024 год"')
    lines.append(f'A: query({_format_kwargs(yo_kwargs, filter_keys)})')

    # group_by
    if group_key:
        gb_kwargs: list[tuple[str, object]] = [("mode", "group_by"), ("resource", res)]
        if metric_key and metric_value and metric_key != group_key:
            gb_kwargs.append((metric_key, metric_value))
        gb_kwargs += [("group_by", group_key), ("year", 2025), ("month", 3)]
        lines.append("")
        lines.append(f'Q: "{topic} по {ex["group_dim"]} за март 2025"')
        lines.append(f'A: query({_format_kwargs(gb_kwargs, filter_keys)})')

        lines.append("")
        lines.append(f'Q: "Топ-5 {ex["group_dim"]} по {topic} за март 2025"')
        lines.append(f'A: query({_format_kwargs(gb_kwargs, filter_keys)})')

    # compare
    if compare_key and compare_values and len(compare_values) == 2:
        cmp_kwargs: list[tuple[str, object]] = [("mode", "compare"), ("resource", res)]
        if metric_key and metric_value and metric_key != compare_key:
            cmp_kwargs.append((metric_key, metric_value))
        cmp_kwargs += [
            ("compare_by", compare_key),
            ("compare_values", compare_values),
            ("year", 2025), ("month", 3),
        ]
        lines.append("")
        lines.append(
            f'Q: "Сравни {compare_values[0]} и {compare_values[1]} '
            f'по {topic} за март 2025"'
        )
        lines.append(f'A: query({_format_kwargs(cmp_kwargs, filter_keys)})')

    lines.append("")
    lines.append("RULES:")
    lines.append("1. ALWAYS call the query tool. NEVER respond with plain text.")
    lines.append("2. Copy enum values EXACTLY from the lists above — do NOT translate, lowercase, or paraphrase.")
    lines.append("3. If a filter value is not mentioned, use its default (Python applies defaults automatically).")
    lines.append("4. Extract year and month from Russian text: 'март 2025' -> year=2025, month=3.")
    lines.append("5. For top-N questions use group_by mode (Python handles limit).")
    lines.append('6. Filter values are ARRAYS. Pass ["Выручка"] for one value, ["ДЗО-1","ДЗО-2"] for many.')
    lines.append("7. For whole-year questions ('за 2024 год') omit 'month' entirely.")

    return "\n".join(lines)
