"""Deterministic query builder: structured params → 1C query.

Takes the JSON produced by param_extractor and builds a valid
1C:Enterprise query string. No LLM involved — pure template logic.

Uses enriched metadata (required, default_value, filter_type, allowed_values)
to ensure all required dimensions appear in WHERE with correct syntax.
"""


def build_query(params: dict, register_metadata: dict) -> dict:
    """Build 1C query from structured parameters.

    Args:
        params: extracted params from param_extractor (resource, filters, period, group_by, etc.)
        register_metadata: register metadata with enriched dimensions

    Returns:
        {"query": str | None, "params": dict, "missing_required": list}
        If missing_required is non-empty, query is None.
    """
    register_name = register_metadata["name"]
    resource = params.get("resource", "Сумма")
    group_by = params.get("group_by", [])
    order_by = params.get("order_by", "desc")
    limit = params.get("limit", 1000)
    filters = params.get("filters", {})
    period = params.get("period", {})

    group_by_set = set(group_by)

    conditions = []
    query_params = {}
    missing_required = []

    for dim in register_metadata.get("dimensions", []):
        dim_name = dim["name"]
        filter_type = dim.get("filter_type", "=")
        required = dim.get("required", False)
        default_value = dim.get("default_value")

        # Dimensions in group_by go to SELECT, not WHERE
        if dim_name in group_by_set:
            continue

        if filter_type == "year_month":
            # Period uses ГОД()/МЕСЯЦ() syntax
            year = (period or {}).get("year")
            month = (period or {}).get("month")
            if year is not None and month is not None:
                conditions.append(f"ГОД({dim_name}) = &Год")
                conditions.append(f"МЕСЯЦ({dim_name}) = &Месяц")
                query_params["Год"] = year
                query_params["Месяц"] = month
            elif required:
                missing_required.append(dim_name)

        elif filter_type == "range":
            # Range uses >= / <= syntax
            start = (period or {}).get("from")
            end = (period or {}).get("to")
            if start is not None:
                conditions.append(f"{dim_name} >= &Начало")
                query_params["Начало"] = start
            if end is not None:
                conditions.append(f"{dim_name} <= &Конец")
                query_params["Конец"] = end
            if required and start is None and end is None:
                missing_required.append(dim_name)

        else:
            # Equality filter: user value > default > missing
            value = filters.get(dim_name)
            if value is None and default_value is not None:
                value = default_value
            if value is not None:
                param_key = dim_name.replace(" ", "_")
                conditions.append(f"{dim_name} = &{param_key}")
                query_params[param_key] = value
            elif required:
                missing_required.append(dim_name)

    # If any required dimension is missing, return error
    if missing_required:
        return {"query": None, "missing_required": missing_required, "params": {}}

    # SELECT clause
    if group_by:
        select_fields = group_by + [f"СУММА({resource}) КАК Значение"]
    else:
        select_fields = [f"СУММА({resource}) КАК Значение"]

    select_clause = ",\n    ".join(select_fields)

    # WHERE clause
    where_clause = ""
    if conditions:
        where_clause = "\nГДЕ\n    " + "\n    И ".join(conditions)

    # GROUP BY
    group_clause = ""
    if group_by:
        group_clause = "\nСГРУППИРОВАТЬ ПО " + ", ".join(group_by)

    # ORDER BY
    order_dir = "УБЫВ" if order_by == "desc" else "ВОЗР"
    order_clause = f"\nУПОРЯДОЧИТЬ ПО Значение {order_dir}"

    query = f"""ВЫБРАТЬ ПЕРВЫЕ {limit}
    {select_clause}
ИЗ
    {register_name}{where_clause}{group_clause}{order_clause}"""

    return {"query": query, "params": query_params, "missing_required": []}
