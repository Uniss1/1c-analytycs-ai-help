# Smart 1C Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move query building from Python to 1C HTTP service, expand from 4 to 7 tools, and harden tool calling reliability.

**Architecture:** Gemma 4 E2B selects a tool and fills JSON params → Python validates and normalizes → 1C HTTP service builds and executes native query → JSON response. No query text crosses the network boundary.

**Tech Stack:** Python 3.11, FastAPI, httpx, pytest, Ollama/OpenAI-compatible API

**Spec:** `docs/superpowers/specs/2026-04-10-smart-1c-backend-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `api/tool_defs.py` | 7 tool definitions (add compare, ratio, filtered) |
| Modify | `api/tool_caller.py` | Retry logic, normalization for 3 new tools |
| Create | `api/param_validator.py` | Fast JSON param validation before 1C call |
| Modify | `api/onec_client.py` | New `execute_tool()` — POST JSON to `/analytics/execute` |
| Modify | `api/main.py` | Simplified flow: tool_caller → param_validator → onec_client.execute_tool |
| Modify | `api/config.py` | No changes needed (URLs/auth already configured) |
| Modify | `scripts/calibrate_tools.py` | Expand from 11 to 20+ test cases |
| Create | `tests/test_tool_defs.py` | Tests for 7 tool schema generation |
| Create | `tests/test_param_validator.py` | Tests for param validation |
| Modify | `tests/test_query_builder.py` | Delete (replaced by test_param_validator) |
| Modify | `tests/test_validator.py` | Delete validate_query tests, keep validate_params via param_validator |
| Delete | `api/query_builder.py` | Logic moves to 1C |
| Delete | `api/query_validator.py` | validate_query no longer needed; validate_params moves to param_validator |

---

### Task 1: Add 3 new tools to `tool_defs.py`

**Files:**
- Modify: `api/tool_defs.py`
- Create: `tests/test_tool_defs.py`

- [ ] **Step 1: Write tests for new tool definitions**

Create `tests/test_tool_defs.py`:

```python
"""Tests for tool_defs — schema generation from register metadata."""

import pytest

from api.tool_defs import build_tools, build_system_message, key_to_dim


@pytest.fixture()
def register_meta():
    return {
        "name": "РегистрСведений.Витрина_Дашборда",
        "description": "Витрина дашборда",
        "dimensions": [
            {
                "name": "Сценарий",
                "data_type": "Строка",
                "required": True,
                "default_value": "Факт",
                "filter_type": "=",
                "allowed_values": ["Факт", "Прогноз", "План"],
            },
            {
                "name": "КонтурПоказателя",
                "data_type": "Строка",
                "required": True,
                "default_value": "свод",
                "filter_type": "=",
                "allowed_values": ["свод", "детализация"],
            },
            {
                "name": "Показатель",
                "data_type": "Строка",
                "required": True,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["Выручка", "ОЗП", "Маржа", "EBITDA"],
            },
            {
                "name": "ДЗО",
                "data_type": "Строка",
                "required": True,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["Консолидация", "ДЗО-1", "ДЗО-2"],
            },
            {
                "name": "Период_Показателя",
                "data_type": "Дата",
                "required": True,
                "default_value": None,
                "filter_type": "year_month",
            },
            {
                "name": "Масштаб",
                "data_type": "Строка",
                "required": False,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["тыс.", "млн."],
            },
        ],
        "resources": [
            {"name": "Сумма", "data_type": "Число"},
        ],
    }


def test_build_tools_returns_7(register_meta):
    tools = build_tools(register_meta)
    assert len(tools) == 7
    names = [t["function"]["name"] for t in tools]
    assert names == ["aggregate", "group_by", "top_n", "time_series", "compare", "ratio", "filtered"]


def test_compare_tool_schema(register_meta):
    tools = build_tools(register_meta)
    compare = next(t for t in tools if t["function"]["name"] == "compare")
    props = compare["function"]["parameters"]["properties"]
    assert "compare_by" in props
    assert "values" in props
    assert props["values"]["type"] == "array"
    assert props["values"]["items"]["type"] == "string"


def test_ratio_tool_schema(register_meta):
    tools = build_tools(register_meta)
    ratio = next(t for t in tools if t["function"]["name"] == "ratio")
    props = ratio["function"]["parameters"]["properties"]
    assert "numerator" in props
    assert "denominator" in props
    # numerator/denominator should have enum from Показатель dimension
    assert "enum" in props["numerator"]
    assert "Выручка" in props["numerator"]["enum"]


def test_filtered_tool_schema(register_meta):
    tools = build_tools(register_meta)
    filtered = next(t for t in tools if t["function"]["name"] == "filtered")
    props = filtered["function"]["parameters"]["properties"]
    assert "condition_operator" in props
    assert "condition_value" in props
    assert set(props["condition_operator"]["enum"]) == {">", "<", ">=", "<=", "="}


def test_key_to_dim_roundtrip():
    assert key_to_dim("scenario") == "Сценарий"
    assert key_to_dim("company") == "ДЗО"
    assert key_to_dim("metric") == "Показатель"
    assert key_to_dim("unknown_key") == "unknown_key"


def test_system_message_contains_rules(register_meta):
    msg = build_system_message(register_meta)
    assert "ALWAYS call one of the provided tools" in msg
    assert "Витрина_Дашборда" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_defs.py -v`
Expected: FAIL — `test_build_tools_returns_7` fails (currently returns 4), compare/ratio/filtered tests fail (tools don't exist).

- [ ] **Step 3: Add compare, ratio, filtered tools to `tool_defs.py`**

Add these tools inside `build_tools()`, after the existing `tool_time_series`. The function currently returns `[tool_aggregate, tool_group_by, tool_top_n, tool_time_series]`.

Add after `tool_time_series` definition (before the `return` statement):

```python
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
```

Update the return statement:

```python
    return [tool_aggregate, tool_group_by, tool_top_n, tool_time_series,
            tool_compare, tool_ratio, tool_filtered]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_defs.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/tool_defs.py tests/test_tool_defs.py
git commit -m "feat: add compare, ratio, filtered tool definitions"
```

---

### Task 2: Create `param_validator.py`

**Files:**
- Create: `api/param_validator.py`
- Create: `tests/test_param_validator.py`

- [ ] **Step 1: Write tests for param validation**

Create `tests/test_param_validator.py`:

```python
"""Tests for param_validator — fast JSON validation before 1C call."""

import pytest

from api.param_validator import validate


@pytest.fixture()
def register_meta():
    return {
        "name": "РегистрСведений.Витрина_Дашборда",
        "resources": [{"name": "Сумма", "data_type": "Число"}],
        "dimensions": [
            {
                "name": "Сценарий",
                "data_type": "Строка",
                "required": True,
                "default_value": "Факт",
                "filter_type": "=",
                "allowed_values": ["Факт", "План", "Прогноз"],
            },
            {
                "name": "Показатель",
                "data_type": "Строка",
                "required": True,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["Выручка", "EBITDA", "Маржа"],
            },
        ],
    }


def test_valid_aggregate(register_meta):
    tool_result = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {"Сценарий": "Факт", "Показатель": "Выручка"},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is True
    assert result.errors == []


def test_invalid_resource(register_meta):
    tool_result = {
        "tool": "aggregate",
        "params": {
            "resource": "НесуществующийРесурс",
            "filters": {},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is False
    assert any("resource" in e.lower() or "ресурс" in e.lower() for e in result.errors)


def test_invalid_year(register_meta):
    tool_result = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {},
            "period": {"year": 1900, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is False
    assert any("year" in e.lower() or "год" in e.lower() for e in result.errors)


def test_invalid_month(register_meta):
    tool_result = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {},
            "period": {"year": 2025, "month": 15},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is False
    assert any("month" in e.lower() or "месяц" in e.lower() for e in result.errors)


def test_invalid_filter_value(register_meta):
    tool_result = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {"Сценарий": "НесуществующийСценарий"},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is False
    assert any("Сценарий" in e for e in result.errors)


def test_compare_needs_two_values(register_meta):
    tool_result = {
        "tool": "compare",
        "params": {
            "resource": "Сумма",
            "compare_by": "Сценарий",
            "values": ["Факт"],  # only 1, need 2
            "filters": {},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is False
    assert any("2" in e or "values" in e.lower() for e in result.errors)


def test_compare_valid(register_meta):
    tool_result = {
        "tool": "compare",
        "params": {
            "resource": "Сумма",
            "compare_by": "Сценарий",
            "values": ["Факт", "План"],
            "filters": {},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is True


def test_filtered_invalid_operator(register_meta):
    tool_result = {
        "tool": "filtered",
        "params": {
            "resource": "Сумма",
            "group_by": "Показатель",
            "condition_operator": "LIKE",
            "condition_value": 100,
            "filters": {},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is False
    assert any("operator" in e.lower() or "оператор" in e.lower() for e in result.errors)


def test_filtered_valid(register_meta):
    tool_result = {
        "tool": "filtered",
        "params": {
            "resource": "Сумма",
            "group_by": "Показатель",
            "condition_operator": ">",
            "condition_value": 100000000,
            "filters": {},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = validate(tool_result, register_meta)
    assert result.ok is True


def test_no_tool_result():
    result = validate({"tool": None, "error": "no tool call"}, {})
    assert result.ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_param_validator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.param_validator'`

- [ ] **Step 3: Implement `param_validator.py`**

Create `api/param_validator.py`:

```python
"""Fast JSON parameter validation before sending to 1C HTTP service.

Catches obvious errors (wrong resource, invalid year/month, bad operator)
without making a network call. 1C does its own deeper validation.
"""

from dataclasses import dataclass, field

VALID_OPERATORS = {">", "<", ">=", "<=", "="}
VALID_TOOLS = {"aggregate", "group_by", "top_n", "time_series", "compare", "ratio", "filtered"}
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

    if tool == "filtered":
        op = params.get("condition_operator")
        if op and op not in VALID_OPERATORS:
            errors.append(f"Неизвестный operator '{op}'. Допустимые: {sorted(VALID_OPERATORS)}")
        val = params.get("condition_value")
        if val is not None and not isinstance(val, (int, float)):
            errors.append(f"condition_value должен быть числом, получено: {type(val).__name__}")

    return ValidationResult(ok=len(errors) == 0, errors=errors)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_param_validator.py -v`
Expected: All 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/param_validator.py tests/test_param_validator.py
git commit -m "feat: add param_validator for pre-1C JSON validation"
```

---

### Task 3: Update `tool_caller.py` — retry logic + new tool normalization

**Files:**
- Modify: `api/tool_caller.py`

- [ ] **Step 1: Write tests for new normalization and retry**

Add to a new file `tests/test_tool_caller.py`:

```python
"""Tests for tool_caller — normalization of new tools (compare, ratio, filtered)."""

from api.tool_caller import _normalize_params


REGISTER_META = {
    "name": "РегистрСведений.Витрина_Дашборда",
    "dimensions": [
        {"name": "Сценарий", "data_type": "Строка", "required": True,
         "default_value": "Факт", "filter_type": "=", "allowed_values": ["Факт", "План"]},
        {"name": "КонтурПоказателя", "data_type": "Строка", "required": True,
         "default_value": "свод", "filter_type": "=", "allowed_values": []},
        {"name": "Показатель", "data_type": "Строка", "required": True,
         "default_value": None, "filter_type": "=", "allowed_values": ["Выручка", "Маржа"]},
        {"name": "ДЗО", "data_type": "Строка", "required": True,
         "default_value": None, "filter_type": "=", "allowed_values": []},
        {"name": "Период_Показателя", "data_type": "Дата", "required": True,
         "default_value": None, "filter_type": "year_month"},
    ],
    "resources": [{"name": "Сумма", "data_type": "Число"}],
}


def test_normalize_compare():
    args = {
        "resource": "Сумма",
        "compare_by": "scenario",
        "values": ["Факт", "План"],
        "year": 2025,
        "month": 3,
    }
    params = _normalize_params("compare", args, REGISTER_META)
    assert params["compare_by"] == "Сценарий"
    assert params["values"] == ["Факт", "План"]
    assert params["period"] == {"year": 2025, "month": 3}


def test_normalize_ratio():
    args = {
        "resource": "Сумма",
        "numerator": "Маржа",
        "denominator": "Выручка",
        "year": 2025,
        "month": 3,
    }
    params = _normalize_params("ratio", args, REGISTER_META)
    assert params["numerator"] == "Маржа"
    assert params["denominator"] == "Выручка"


def test_normalize_filtered():
    args = {
        "resource": "Сумма",
        "group_by": "company",
        "condition_operator": ">",
        "condition_value": 100000000,
        "year": 2025,
        "month": 3,
    }
    params = _normalize_params("filtered", args, REGISTER_META)
    assert params["group_by"] == ["ДЗО"]
    assert params["condition_operator"] == ">"
    assert params["condition_value"] == 100000000


def test_normalize_aggregate_unchanged():
    """Existing aggregate normalization still works."""
    args = {
        "resource": "Сумма",
        "metric": "Выручка",
        "scenario": "Факт",
        "year": 2025,
        "month": 3,
    }
    params = _normalize_params("aggregate", args, REGISTER_META)
    assert params["filters"]["Показатель"] == "Выручка"
    assert params["filters"]["Сценарий"] == "Факт"
    assert params["period"] == {"year": 2025, "month": 3}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_caller.py -v`
Expected: FAIL — `test_normalize_compare` fails (compare_by not handled), `test_normalize_ratio` fails (numerator/denominator not handled).

- [ ] **Step 3: Update `_normalize_params` in `tool_caller.py`**

In `api/tool_caller.py`, update `_normalize_params()`. Add handling for new tools. The key changes:

1. Add `compare_by`, `values`, `numerator`, `denominator`, `condition_operator`, `condition_value` to `skip_keys` so they don't end up in `filters`
2. Add tool-specific normalization after the existing logic

Replace the `_normalize_params` function:

```python
def _normalize_params(tool_name: str, args: dict, register_metadata: dict) -> dict:
    """Convert tool call arguments to format for 1C HTTP service.

    Tool args use Latin keys (metric, scenario, company, year, month).
    1C expects native names (Показатель, Сценарий, ДЗО) + period dict.
    """
    resource = args.get("resource", "Сумма")
    year = args.get("year")
    month = args.get("month")
    group_by_latin = args.get("group_by")
    order_by = args.get("order", args.get("order_by", "desc"))
    limit = args.get("limit", 1000)

    # Build period from flat year/month
    period = {}
    if year is not None and month is not None:
        period = {"year": year, "month": month}

    # Keys handled explicitly — don't put them into filters
    skip_keys = {
        "resource", "year", "month", "group_by", "order", "order_by", "limit",
        "compare_by", "values", "numerator", "denominator",
        "condition_operator", "condition_value",
    }

    # Convert Latin filter keys → 1C dimension names
    filters = {}
    for k, v in args.items():
        if k in skip_keys or v is None:
            continue
        dim_name = key_to_dim(k)
        filters[dim_name] = v

    # Apply defaults for required dimensions not provided
    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        if dim.get("filter_type") in ("year_month", "range"):
            continue
        if name not in filters and dim.get("default_value"):
            filters[name] = dim["default_value"]

    # group_by: convert Latin key to 1C name
    group_by = []
    if group_by_latin:
        group_by = [key_to_dim(group_by_latin)]

    # Top N defaults
    if tool_name == "top_n":
        limit = limit if limit != 1000 else 10

    # Base params (shared by all tools)
    params = {
        "resource": resource,
        "filters": filters,
        "period": period,
        "group_by": group_by,
        "order_by": order_by,
        "limit": limit,
    }

    # Tool-specific params
    if tool_name == "compare":
        params["compare_by"] = key_to_dim(args.get("compare_by", ""))
        params["values"] = args.get("values", [])

    elif tool_name == "ratio":
        params["numerator"] = args.get("numerator", "")
        params["denominator"] = args.get("denominator", "")

    elif tool_name == "filtered":
        params["condition_operator"] = args.get("condition_operator", ">")
        params["condition_value"] = args.get("condition_value", 0)

    # Determine needs_clarification
    needs_clarification = False
    missing = []
    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        if not dim.get("required"):
            continue
        if dim.get("default_value"):
            continue
        ft = dim.get("filter_type", "=")
        if ft in ("year_month", "range"):
            if not period.get("year"):
                missing.append(name)
        elif ft == "=":
            if name not in filters and name not in group_by:
                # For compare/ratio, some dimensions may be covered by values/numerator/denominator
                if tool_name == "compare" and name == key_to_dim(args.get("compare_by", "")):
                    continue
                missing.append(name)

    if missing:
        needs_clarification = True

    params["needs_clarification"] = needs_clarification
    params["understood"] = {
        "описание": f"tool={tool_name}, resource={resource}",
    }

    return params
```

- [ ] **Step 4: Add retry logic for missing tool call in `call_with_tools`**

In `api/tool_caller.py`, update `call_with_tools()`. After parsing the response, if no tool call was returned, retry once with a reinforced prompt. Replace the response parsing section inside the for loop:

```python
            data = response.json()
            parsed = _parse_response(data, register_metadata)

            # Retry once if model didn't make a tool call
            if parsed.get("tool") is None and attempt < MAX_RETRIES:
                logger.warning("No tool call on attempt %d, retrying", attempt)
                # Add reinforcement message
                payload["messages"].append({
                    "role": "assistant",
                    "content": parsed.get("error", ""),
                })
                payload["messages"].append({
                    "role": "user",
                    "content": (
                        "You MUST call one of the provided tools. "
                        "Do NOT respond with text. Call a tool now."
                    ),
                })
                continue

            return parsed
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_tool_caller.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/tool_caller.py tests/test_tool_caller.py
git commit -m "feat: tool_caller handles compare/ratio/filtered + retry on no tool call"
```

---

### Task 4: Update `onec_client.py` — add `execute_tool()`

**Files:**
- Modify: `api/onec_client.py`

- [ ] **Step 1: Write test for `execute_tool`**

Create `tests/test_onec_client.py`:

```python
"""Tests for onec_client — execute_tool sends JSON to 1C."""

import pytest
import httpx

from api.onec_client import execute_tool


@pytest.fixture()
def mock_1c_success(monkeypatch):
    """Mock httpx to return a successful 1C response."""
    async def mock_post(self, url, **kwargs):
        assert "/analytics/execute" in url
        body = kwargs.get("json", {})
        assert "register" in body
        assert "tool" in body
        assert "params" in body
        resp = httpx.Response(
            200,
            json={
                "success": True,
                "data": [{"Сценарий": "Факт", "Значение": 150}],
                "computed": None,
            },
            request=httpx.Request("POST", url),
        )
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)


@pytest.fixture()
def mock_1c_error(monkeypatch):
    """Mock httpx to return a 1C validation error."""
    async def mock_post(self, url, **kwargs):
        resp = httpx.Response(
            200,
            json={
                "success": False,
                "error_type": "invalid_params",
                "error_message": "Неизвестное значение",
                "allowed_values": ["Факт", "План"],
            },
            request=httpx.Request("POST", url),
        )
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)


@pytest.mark.asyncio
async def test_execute_tool_success(mock_1c_success):
    tool_result = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {"Сценарий": "Факт"},
            "period": {"year": 2025, "month": 3},
        },
    }
    result = await execute_tool(
        tool_result,
        register_name="РегистрСведений.Витрина_Дашборда",
    )
    assert result["success"] is True
    assert len(result["data"]) == 1


@pytest.mark.asyncio
async def test_execute_tool_error(mock_1c_error):
    tool_result = {
        "tool": "aggregate",
        "params": {"resource": "Сумма", "filters": {}, "period": {}},
    }
    result = await execute_tool(
        tool_result,
        register_name="РегистрСведений.Витрина_Дашборда",
    )
    assert result["success"] is False
    assert result["error_type"] == "invalid_params"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_onec_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'execute_tool'`

- [ ] **Step 3: Add `execute_tool` to `onec_client.py`**

In `api/onec_client.py`, add the new function after the existing `execute_query`:

```python
async def execute_tool(tool_result: dict, register_name: str) -> dict:
    """Execute tool via 1C HTTP service (JSON params, no query text).

    POST to /analytics/execute with:
        {"register": str, "tool": str, "params": dict}

    Returns: 1C response dict:
        success case: {"success": True, "data": [...], "computed": {...}}
        error case:   {"success": False, "error_type": str, "error_message": str, ...}
    """
    payload = {
        "register": register_name,
        "tool": tool_result["tool"],
        "params": tool_result["params"],
    }

    async with httpx.AsyncClient(timeout=settings.query_timeout) as client:
        response = await client.post(
            f"{settings.onec_base_url}/analytics/execute",
            json=payload,
            auth=(settings.onec_user, settings.onec_password),
        )
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_onec_client.py -v`
Expected: All 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/onec_client.py tests/test_onec_client.py
git commit -m "feat: onec_client.execute_tool sends JSON params to 1C"
```

---

### Task 5: Update `main.py` — new flow

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: Update imports in `main.py`**

Replace the imports section. Remove `query_builder` and `query_validator` imports, add `tool_caller`, `param_validator`:

```python
# Remove these lines:
from .param_extractor import extract_params
from .query_builder import build_query
from .query_validator import validate_params, validate_query

# Add these lines:
from .tool_caller import call_with_tools
from .param_validator import validate as validate_tool_params
from .onec_client import execute_tool
```

Keep the existing `execute_query` import — it stays for backward compatibility until 1C endpoint is deployed. But update the import line to also include `execute_tool`:

```python
from .onec_client import execute_query, execute_tool
```

- [ ] **Step 2: Rewrite `_handle_data` function**

Replace the `_handle_data` function in `main.py`:

```python
async def _handle_data(
    message: str,
    dashboard_context: dict | None,
    dashboard_slug: str | None,
    session_id: str,
    debug: dict,
) -> dict:
    """Data flow: metadata → tool calling → validate → execute in 1C."""
    # Find register
    t0 = time.monotonic()
    register_meta, meta_debug = find_register(message, dashboard_context)
    ms = int((time.monotonic() - t0) * 1000)
    if not register_meta:
        debug["steps"].append({
            "step": "metadata",
            "found": False,
            "extracted_words": meta_debug.get("extracted_words"),
            "available_keywords": meta_debug.get("available_keywords"),
            "matching_keywords": meta_debug.get("matching_keywords"),
            "ms": ms,
        })
        return {"answer": "Не удалось определить подходящий регистр для вашего вопроса."}

    register_name = register_meta["name"]
    debug["steps"].append({
        "step": "metadata",
        "found": True,
        "register": register_name,
        "extracted_words": meta_debug.get("extracted_words"),
        "matching_keywords": meta_debug.get("matching_keywords"),
        "dimensions": [d["name"] for d in register_meta.get("dimensions", [])],
        "resources": [r["name"] for r in register_meta.get("resources", [])],
        "ms": ms,
    })

    # Tool calling via Gemma
    t0 = time.monotonic()
    tool_result = await call_with_tools(message, register_meta)
    debug["steps"].append({
        "step": "tool_caller",
        "tool": tool_result.get("tool"),
        "args": tool_result.get("args"),
        "error": tool_result.get("error"),
        "ms": int((time.monotonic() - t0) * 1000),
    })

    if not tool_result.get("tool"):
        return {
            "answer": "Не удалось обработать вопрос. Попробуйте переформулировать.",
            "register_name": register_name,
        }

    # Normalize result for 1C
    params = tool_result.get("params", {})

    # Check if clarification needed (missing required params)
    if params.get("needs_clarification"):
        _pending_clarifications[session_id] = {
            "params": params,
            "register_metadata": register_meta,
            "tool": tool_result["tool"],
        }
        # Build clarification text from missing dimensions
        dims_by_name = {d["name"]: d for d in register_meta.get("dimensions", [])}
        lines = ["Уточните, пожалуйста:"]
        for dim in register_meta.get("dimensions", []):
            name = dim["name"]
            if not dim.get("required") or dim.get("default_value"):
                continue
            ft = dim.get("filter_type", "=")
            if ft in ("year_month", "range") and not params.get("period", {}).get("year"):
                lines.append(f"- {name}: укажите год и месяц")
            elif ft == "=" and name not in params.get("filters", {}) and name not in params.get("group_by", []):
                allowed = dim.get("allowed_values", [])
                if allowed:
                    lines.append(f"- {name}: выберите из {allowed}")
                else:
                    lines.append(f"- {name}: укажите значение")
        return {
            "answer": "\n".join(lines),
            "register_name": register_name,
            "needs_clarification": True,
        }

    # Validate params before sending to 1C
    validation = validate_tool_params(tool_result, register_meta)
    if not validation.ok:
        debug["steps"].append({"step": "param_validation", "errors": validation.errors})
        return {
            "answer": "Некорректные параметры:\n" + "\n".join(f"- {e}" for e in validation.errors),
            "register_name": register_name,
            "needs_clarification": True,
        }

    # Execute in 1C via new JSON endpoint
    t0 = time.monotonic()
    try:
        onec_result = await execute_tool(tool_result, register_name=register_name)
    except Exception as e:
        logger.error("1C execute_tool failed: %s", e)
        debug["steps"].append({"step": "1c_execute", "error": str(e), "ms": int((time.monotonic() - t0) * 1000)})
        return {
            "answer": f"Ошибка выполнения запроса к 1С: {e}",
            "register_name": register_name,
        }

    exec_ms = int((time.monotonic() - t0) * 1000)

    if not onec_result.get("success"):
        error_type = onec_result.get("error_type", "unknown")
        error_msg = onec_result.get("error_message", "Неизвестная ошибка")
        debug["steps"].append({"step": "1c_execute", "success": False, "error_type": error_type, "error": error_msg, "ms": exec_ms})

        if error_type == "no_data":
            return {"answer": "Данные за указанный период не найдены.", "register_name": register_name}

        return {
            "answer": f"Ошибка: {error_msg}",
            "register_name": register_name,
            "needs_clarification": error_type in ("invalid_params", "missing_params"),
        }

    data = onec_result.get("data", [])
    computed = onec_result.get("computed")
    debug["steps"].append({
        "step": "1c_execute",
        "success": True,
        "rows": len(data),
        "computed": computed,
        "sample": data[:5],
        "ms": exec_ms,
    })

    if not data and not computed:
        return {"answer": "Данные за указанный период не найдены.", "register_name": register_name}

    # Format response
    t0 = time.monotonic()
    # Include computed values in data for formatter
    format_data = data
    if computed:
        format_data = {"rows": data, "computed": computed}
    answer, fmt_debug = await format_response(message, format_data, register_name)
    debug["steps"].append({
        "step": "formatter",
        "raw_data_rows": len(data),
        "raw_llm_response": fmt_debug.get("raw_llm_response"),
        "ms": int((time.monotonic() - t0) * 1000),
    })

    return {
        "answer": answer,
        "register_name": register_name,
        "query_text": onec_result.get("query_text"),
    }
```

- [ ] **Step 3: Remove `_execute_query_flow` function**

Delete the entire `_execute_query_flow` function (lines 341-474 in current `main.py`). It is replaced by the new `_handle_data`.

- [ ] **Step 4: Update `_handle_clarification_response` to use tool_caller**

Replace the `_handle_clarification_response` function. The key change is using `call_with_tools` instead of `extract_params`:

```python
async def _handle_clarification_response(
    message: str,
    session_id: str,
    dashboard_slug: str | None,
    start: float,
    debug: dict,
) -> ChatResponse:
    """Handle user's response to a clarification question."""
    pending = _pending_clarifications.pop(session_id)
    register_meta = pending["register_metadata"]
    register_name = register_meta["name"]

    debug["steps"].append({"step": "clarification_response", "pending_tool": pending.get("tool")})

    # Re-run tool calling with combined context
    original_desc = pending.get("params", {}).get("understood", {}).get("описание", "")
    combined = f"{original_desc}. Уточнение: {message}" if original_desc else message

    t0 = time.monotonic()
    tool_result = await call_with_tools(combined, register_meta)
    debug["steps"].append({
        "step": "tool_caller_retry",
        "tool": tool_result.get("tool"),
        "args": tool_result.get("args"),
        "ms": int((time.monotonic() - t0) * 1000),
    })

    if not tool_result.get("tool"):
        latency = int((time.monotonic() - start) * 1000)
        answer = "Не удалось обработать уточнение. Попробуйте задать вопрос заново."
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name, debug=debug,
        )

    # Validate and execute
    params = tool_result.get("params", {})
    validation = validate_tool_params(tool_result, register_meta)
    if not validation.ok:
        latency = int((time.monotonic() - start) * 1000)
        answer = "Некорректные параметры:\n" + "\n".join(f"- {e}" for e in validation.errors)
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name,
            needs_clarification=True, debug=debug,
        )

    t0 = time.monotonic()
    try:
        onec_result = await execute_tool(tool_result, register_name=register_name)
    except Exception as e:
        logger.error("1C execute_tool failed on clarification: %s", e)
        latency = int((time.monotonic() - start) * 1000)
        answer = f"Ошибка выполнения запроса к 1С: {e}"
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name, debug=debug,
        )

    if not onec_result.get("success"):
        latency = int((time.monotonic() - start) * 1000)
        answer = onec_result.get("error_message", "Ошибка выполнения")
        save_message(session_id, "user", message)
        save_message(session_id, "assistant", answer, intent="data", latency_ms=latency)
        return ChatResponse(
            answer=answer, intent="data", session_id=session_id,
            latency_ms=latency, register_name=register_name, debug=debug,
        )

    data = onec_result.get("data", [])
    computed = onec_result.get("computed")

    if not data and not computed:
        answer = "Данные за указанный период не найдены."
    else:
        format_data = data
        if computed:
            format_data = {"rows": data, "computed": computed}
        answer, _ = await format_response(message, format_data, register_name)

    latency = int((time.monotonic() - start) * 1000)
    query_text = onec_result.get("query_text")
    debug["total_ms"] = latency

    save_message(session_id, "user", message)
    save_message(session_id, "assistant", answer,
                 intent="data", register=register_name,
                 query_text=query_text, latency_ms=latency)
    save_cache(message, answer, "data", dashboard_slug)

    return ChatResponse(
        answer=answer, intent="data", session_id=session_id,
        latency_ms=latency, register_name=register_name, debug=debug,
    )
```

- [ ] **Step 5: Run existing tests to check nothing is broken**

Run: `pytest tests/ -v --ignore=tests/test_query_builder.py --ignore=tests/test_validator.py`
Expected: All tests PASS (test_query_builder and test_validator are excluded — they test deleted modules).

- [ ] **Step 6: Commit**

```bash
git add api/main.py
git commit -m "feat: main.py uses tool_caller + param_validator + execute_tool flow"
```

---

### Task 6: Expand calibration script

**Files:**
- Modify: `scripts/calibrate_tools.py`

- [ ] **Step 1: Add new test cases to `TEST_CASES`**

In `scripts/calibrate_tools.py`, append these entries to the `TEST_CASES` list:

```python
    # --- New tools ---

    # Compare
    (
        "Сравни факт и план по выручке за март 2025",
        "compare",
        {"resource": "Сумма", "compare_by": "scenario", "values": ["Факт", "План"],
         "metric": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Факт vs бюджет EBITDA за январь 2025",
        "compare",
        {"compare_by": "scenario", "metric": "EBITDA", "year": 2025, "month": 1},
    ),

    # Ratio
    (
        "Рентабельность за март 2025",
        "ratio",
        {"numerator": "Маржа", "denominator": "Выручка", "year": 2025, "month": 3},
    ),
    (
        "Маржа к выручке за январь 2025",
        "ratio",
        {"numerator": "Маржа", "denominator": "Выручка", "year": 2025, "month": 1},
    ),

    # Filtered
    (
        "ДЗО где выручка больше 100 млн за март 2025",
        "filtered",
        {"group_by": "company", "condition_operator": ">", "metric": "Выручка",
         "year": 2025, "month": 3},
    ),
    (
        "Показатели с суммой меньше 10 млн за январь 2025",
        "filtered",
        {"group_by": "metric", "condition_operator": "<", "year": 2025, "month": 1},
    ),

    # --- Negative / edge cases ---

    # Missing period — should still make a tool call (validation catches it later)
    (
        "Какая выручка?",
        "aggregate",
        {"resource": "Сумма", "metric": "Выручка"},
    ),
]
```

- [ ] **Step 2: Run calibration**

Run: `python3 scripts/calibrate_tools.py --model gemma4:e2b -v`
Expected: Review results — new tools may need description tuning. Note any failures.

- [ ] **Step 3: Commit**

```bash
git add scripts/calibrate_tools.py
git commit -m "feat: calibration script expanded to 18 test cases with new tools"
```

---

### Task 7: Clean up deleted modules

**Files:**
- Delete: `api/query_builder.py`
- Delete: `api/query_validator.py`
- Delete: `tests/test_query_builder.py`
- Modify: `tests/test_validator.py` → delete file (validate_params moved to param_validator)
- Delete: `api/param_extractor.py` (replaced by tool_caller)
- Delete: `tests/test_param_extractor.py`

- [ ] **Step 1: Verify no other files import deleted modules**

Run:
```bash
grep -r "from.*query_builder import\|from.*query_validator import\|from.*param_extractor import" api/ tests/ --include="*.py"
```

Expected: Only `main.py` (already updated in Task 5) and the test files being deleted. If any other file imports them, update that file first.

- [ ] **Step 2: Delete the files**

```bash
git rm api/query_builder.py api/query_validator.py api/param_extractor.py
git rm tests/test_query_builder.py tests/test_validator.py tests/test_param_extractor.py
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All remaining tests PASS. No import errors.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: remove query_builder, query_validator, param_extractor — logic moved to 1C + param_validator"
```

---

### Task 8: Documentation — 1C HTTP service spec

**Files:**
- Create: `docs/1c-http-service-spec.md`

This task produces a standalone spec for the 1C developer to implement the HTTP service.

- [ ] **Step 1: Write the 1C spec**

Create `docs/1c-http-service-spec.md`:

```markdown
# 1С HTTP-сервис: спецификация эндпоинта /analytics/execute

## Назначение

Принимает JSON с именем инструмента и параметрами, собирает и выполняет
1С-запрос, возвращает результат в JSON.

## Эндпоинт

**POST** `/analytics/execute`

**Content-Type:** `application/json`
**Авторизация:** Basic Auth (те же credentials, что и для `/query`)

## Формат запроса

```json
{
  "register": "РегистрСведений.Витрина_Дашборда",
  "tool": "aggregate | group_by | top_n | time_series | compare | ratio | filtered",
  "params": {
    "resource": "Сумма",
    "filters": {
      "Сценарий": "Факт",
      "КонтурПоказателя": "свод",
      "Показатель": "Выручка",
      "ДЗО": "Консолидация"
    },
    "period": {
      "year": 2025,
      "month": 3
    },
    "group_by": ["ДЗО"],
    "order_by": "desc",
    "limit": 1000,

    // compare-specific:
    "compare_by": "Сценарий",
    "values": ["Факт", "План"],

    // ratio-specific:
    "numerator": "Маржа",
    "denominator": "Выручка",

    // filtered-specific:
    "condition_operator": ">",
    "condition_value": 100000000
  }
}
```

## Обработчики по инструментам

### aggregate

Простая агрегация. Запрос:

```1c
ВЫБРАТЬ ПЕРВЫЕ <limit>
    СУММА(<resource>) КАК Значение
ИЗ
    <register>
ГДЕ
    <условия из filters + period>
УПОРЯДОЧИТЬ ПО Значение <order_by>
```

### group_by

Группировка по измерению. Запрос:

```1c
ВЫБРАТЬ ПЕРВЫЕ <limit>
    <group_by>,
    СУММА(<resource>) КАК Значение
ИЗ
    <register>
ГДЕ
    <условия>
СГРУППИРОВАТЬ ПО <group_by>
УПОРЯДОЧИТЬ ПО Значение <order_by>
```

### top_n

То же, что group_by, но limit по умолчанию = 10.

### time_series

Группировка по ГОД() + МЕСЯЦ(). Period опционален (без period — все периоды).

```1c
ВЫБРАТЬ ПЕРВЫЕ <limit>
    ГОД(<date_dim>) КАК Год,
    МЕСЯЦ(<date_dim>) КАК Месяц,
    СУММА(<resource>) КАК Значение
ИЗ
    <register>
ГДЕ
    <условия без period>
СГРУППИРОВАТЬ ПО ГОД(<date_dim>), МЕСЯЦ(<date_dim>)
УПОРЯДОЧИТЬ ПО Год, Месяц
```

### compare

Сравнение двух значений одного измерения.

```1c
ВЫБРАТЬ ПЕРВЫЕ <limit>
    <compare_by>,
    СУММА(<resource>) КАК Значение
ИЗ
    <register>
ГДЕ
    <compare_by> В (&Значения)
    И <остальные условия>
СГРУППИРОВАТЬ ПО <compare_by>
```

Параметр `Значения` = массив `values`.

В ответе `computed`: `diff` (второе минус первое), `percent` ((diff / первое) * 100).

### ratio

Отношение двух показателей.

```1c
ВЫБРАТЬ ПЕРВЫЕ <limit>
    Показатель,
    СУММА(<resource>) КАК Значение
ИЗ
    <register>
ГДЕ
    Показатель В (&Значения)
    И <остальные условия>
СГРУППИРОВАТЬ ПО Показатель
```

Параметр `Значения` = `[numerator, denominator]`.

В ответе `computed`: `ratio` = значение numerator / значение denominator.
Проверка деления на ноль → `error_type: "no_data"`.

### filtered

Фильтрация по значению агрегата (HAVING).

```1c
ВЫБРАТЬ ПЕРВЫЕ <limit>
    <group_by>,
    СУММА(<resource>) КАК Значение
ИЗ
    <register>
ГДЕ
    <условия>
СГРУППИРОВАТЬ ПО <group_by>
ИМЕЮЩИЕ СУММА(<resource>) <operator> &Порог
УПОРЯДОЧИТЬ ПО Значение <order_by>
```

**ВАЖНО:** оператор подставлять через switch/case, не конкатенацией:

```1c
Если Оператор = ">" Тогда
    ТекстУсловия = "СУММА(" + Ресурс + ") > &Порог";
ИначеЕсли Оператор = "<" Тогда
    ...
```

## Формат ответа

### Успех

```json
{
  "success": true,
  "data": [{"Сценарий": "Факт", "Значение": 150000000}],
  "computed": {"diff": -50000000, "percent": -25.0},
  "query_text": "ВЫБРАТЬ ..."
}
```

`computed` — только для compare/ratio. Для остальных — `null`.
`query_text` — опционально, для отладки.

### Ошибка

```json
{
  "success": false,
  "error_type": "invalid_params | missing_params | no_data | execution_error",
  "error_message": "Человекочитаемое описание ошибки",
  "allowed_values": ["Факт", "План"]
}
```

`allowed_values` — только для `invalid_params`, иначе отсутствует.

## Общая функция: ПостроитьУсловияОтбора

Принимает `filters` (dict) и `period` ({year, month}).

Строит текст ГДЕ-части:
- Для каждого filter: `<имя> = &<имя>`
- Для period: `ГОД(<date_dim>) = &Год И МЕСЯЦ(<date_dim>) = &Месяц`

Устанавливает параметры через `Запрос.УстановитьПараметр()`.

## Валидация

`ВалидироватьПараметры()` перед сборкой запроса:
- register существует
- Имена измерений/ресурсов в params соответствуют метаданным
- Значения filters проверяются против допустимых
- При ошибке: вернуть `{"success": false, "error_type": "invalid_params", ...}`
```

- [ ] **Step 2: Commit**

```bash
git add docs/1c-http-service-spec.md
git commit -m "docs: 1C HTTP service spec for /analytics/execute endpoint"
```
