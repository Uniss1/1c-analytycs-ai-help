"""Dynamic tool definitions for Gemma 4 E2B tool calling.

Builds OpenAI-compatible tool schemas from register metadata.
Each tool represents a query template type (aggregate, group_by, top_n, time_series).
The model selects the right tool and fills parameters in one call.

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
    required = []

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


def _has_required_period(register_metadata: dict) -> bool:
    """Check if register has a required date dimension."""
    for dim in register_metadata.get("dimensions", []):
        if dim.get("filter_type") in ("year_month", "range") and dim.get("required"):
            return True
    return False


def build_tools(register_metadata: dict) -> list[dict]:
    """Build OpenAI-compatible tool definitions from register metadata."""
    filter_props, filter_required = _filter_properties(register_metadata)
    resources = _resource_enum(register_metadata)
    groupable = _groupable_dimensions(register_metadata)
    period_required = _has_required_period(register_metadata)

    # Common properties shared across all tools
    base_props = {
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
    }

    base_required = ["resource"] + filter_required
    if period_required:
        base_required.extend(["year", "month"])

    # Tool 1: aggregate
    tool_aggregate = {
        "type": "function",
        "function": {
            "name": "aggregate",
            "description": (
                "Get a single aggregated sum of a metric for a specific period. "
                "Use for: 'какая выручка за...', 'сколько', 'сумма за', 'итого за', "
                "'прогноз на [месяц]', 'план по [месяц]'. "
                "NOT for dynamics/trends — use time_series for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {**base_props},
                "required": base_required,
            },
        },
    }

    # Tool 2: group_by
    group_props = {
        **base_props,
        "group_by": {
            "type": "string",
            "enum": groupable,
            "description": (
                "Dimension to group by. "
                "Use for: 'по ДЗО' → company, 'по показателям' → metric, "
                "'по сценариям' → scenario, 'в разрезе' → pick dimension"
            ),
        },
        "order": {
            "type": "string",
            "enum": ["desc", "asc"],
            "description": "Sort order. Default: desc",
        },
    }
    tool_group_by = {
        "type": "function",
        "function": {
            "name": "group_by",
            "description": (
                "Get values grouped by a dimension (GROUP BY). "
                "Use for: 'по ДЗО', 'по подразделениям', 'по показателям', "
                "'в разрезе', 'по организациям'"
            ),
            "parameters": {
                "type": "object",
                "properties": group_props,
                "required": base_required + ["group_by"],
            },
        },
    }

    # Tool 3: top_n
    top_props = {
        **base_props,
        "group_by": {
            "type": "string",
            "enum": groupable,
            "description": "Dimension to rank by",
        },
        "limit": {
            "type": "integer",
            "description": "Number of top results (N from 'top-N'). Default: 10",
        },
        "order": {
            "type": "string",
            "enum": ["desc", "asc"],
            "description": "desc = best/highest, asc = worst/lowest",
        },
    }
    tool_top_n = {
        "type": "function",
        "function": {
            "name": "top_n",
            "description": (
                "Get TOP-N ranked results. "
                "Use for: 'топ-5', 'top 10', 'лучших', 'худших'"
            ),
            "parameters": {
                "type": "object",
                "properties": top_props,
                "required": base_required + ["group_by"],
            },
        },
    }

    # Tool 4: time_series
    ts_props = {**base_props}
    # For time_series, year/month are optional (shows all periods)
    ts_required = [r for r in base_required if r not in ("year", "month")]
    tool_time_series = {
        "type": "function",
        "function": {
            "name": "time_series",
            "description": (
                "Show dynamics over time periods (monthly). "
                "Use for: 'по месяцам', 'динамика', 'тренд', 'помесячно'"
            ),
            "parameters": {
                "type": "object",
                "properties": ts_props,
                "required": ts_required,
            },
        },
    }

    # --- Metric enum for ratio tool ---
    metric_dim = next(
        (d for d in register_metadata.get("dimensions", [])
         if d["name"] == "Показатель"),
        None,
    )
    metric_values = (
        [str(v) for v in metric_dim.get("allowed_values", [])]
        if metric_dim and metric_dim.get("allowed_values")
        else []
    )

    # Tool 5: compare
    compare_props = {
        **base_props,
        "compare_by": {
            "type": "string",
            "enum": groupable,
            "description": (
                "Dimension to compare across. "
                "Use for: 'факт vs план' → scenario, "
                "'ДЗО-1 vs ДЗО-2' → company"
            ),
        },
        "values": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Exactly 2 values to compare. "
                "E.g. ['Факт', 'План'] or ['ДЗО-1', 'ДЗО-2']"
            ),
        },
    }
    tool_compare = {
        "type": "function",
        "function": {
            "name": "compare",
            "description": (
                "Compare two values of the same dimension side by side. "
                "Use for: 'факт vs план', 'сравни', 'разница между', "
                "'план и факт', 'бюджет vs прогноз'"
            ),
            "parameters": {
                "type": "object",
                "properties": compare_props,
                "required": base_required + ["compare_by", "values"],
            },
        },
    }

    # Tool 6: ratio
    ratio_props = {**base_props}
    if metric_values:
        ratio_props["numerator"] = {
            "type": "string",
            "enum": metric_values,
            "description": "Metric for numerator (top of fraction). E.g. 'Маржа'",
        }
        ratio_props["denominator"] = {
            "type": "string",
            "enum": metric_values,
            "description": "Metric for denominator (bottom of fraction). E.g. 'Выручка'",
        }
    else:
        ratio_props["numerator"] = {
            "type": "string",
            "description": "Metric for numerator (top of fraction)",
        }
        ratio_props["denominator"] = {
            "type": "string",
            "description": "Metric for denominator (bottom of fraction)",
        }
    tool_ratio = {
        "type": "function",
        "function": {
            "name": "ratio",
            "description": (
                "Calculate ratio of two metrics (numerator / denominator). "
                "Use for: 'рентабельность', 'доля', 'отношение', "
                "'маржа к выручке', 'процент от'"
            ),
            "parameters": {
                "type": "object",
                "properties": ratio_props,
                "required": base_required + ["numerator", "denominator"],
            },
        },
    }

    # Tool 7: filtered
    filtered_props = {
        **base_props,
        "group_by": {
            "type": "string",
            "enum": groupable,
            "description": "Dimension to group by before applying filter condition",
        },
        "condition_operator": {
            "type": "string",
            "enum": [">", "<", ">=", "<=", "="],
            "description": (
                "Comparison operator for the aggregate value. "
                "'больше' → '>', 'меньше' → '<', 'не менее' → '>=', "
                "'не более' → '<=', 'равно' → '='"
            ),
        },
        "condition_value": {
            "type": "number",
            "description": (
                "Threshold number. Convert text to number: "
                "'100 млн' → 100000000, '1.5 млрд' → 1500000000, "
                "'50 тыс' → 50000"
            ),
        },
    }
    tool_filtered = {
        "type": "function",
        "function": {
            "name": "filtered",
            "description": (
                "Filter grouped results by aggregate value (HAVING clause). "
                "Use for: 'где выручка больше 100 млн', 'с суммой менее', "
                "'превышает', 'ниже порога'"
            ),
            "parameters": {
                "type": "object",
                "properties": filtered_props,
                "required": base_required + ["group_by", "condition_operator", "condition_value"],
            },
        },
    }

    return [tool_aggregate, tool_group_by, tool_top_n, tool_time_series,
            tool_compare, tool_ratio, tool_filtered]


def build_system_message(register_metadata: dict) -> str:
    """Build system message for the model."""
    name = register_metadata.get("name", "")
    desc = register_metadata.get("description", "")

    lines = [
        "You are an analytics assistant. Users ask questions about 1C register data.",
        f"Register: {name}",
    ]
    if desc:
        lines.append(f"Description: {desc}")

    lines.append("")
    lines.append("RULES:")
    lines.append("1. ALWAYS call one of the provided tools. NEVER respond with text.")
    lines.append("2. Pick values STRICTLY from the allowed enums.")
    lines.append("3. If a value is not mentioned in the question, use the default from the parameter description.")
    lines.append("4. Extract year and month from Russian text: 'март 2025' → year=2025, month=3.")
    lines.append("5. For 'топ-N' questions use the top_n tool with limit=N.")
    lines.append("6. For 'по ДЗО/показателям/сценариям' use the group_by tool.")
    lines.append("7. For 'динамика/по месяцам/тренд' use the time_series tool.")
    lines.append("8. For simple 'какая выручка/сколько' use the aggregate tool.")

    return "\n".join(lines)
