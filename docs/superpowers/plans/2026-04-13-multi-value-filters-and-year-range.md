# Multi-value filters + year-only period — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tool schema принимает массивы для фильтров и опциональный `month`; 1С-контракт переходит на массивы для filters; имя регистра в YAML без префикса типа; доки обновлены.

**Architecture:** Schema filter-измерений становится `type: array, items: {string, enum}`; `month` убираем из `required`. В Python нормализация терпит строку (оборачивает в одноэлементный массив) и пустой массив (отбрасывает). В 1С BSL `ПостроитьУсловияОтбора` всегда строит `<Имя> В (&Знач)`. Имя регистра в payload — только идентификатор (`Витрина_Дашборда`).

**Tech Stack:** Python 3.11+, pytest/pytest-asyncio/respx, FastAPI, Ollama, 1C BSL.

**Spec:** `docs/superpowers/specs/2026-04-13-multi-value-filters-and-year-range.md`

---

## Task 1: Schema — filter dims становятся массивами; month опционален

**Files:**
- Modify: `api/tool_defs.py` (функция `_filter_properties`, список `required` в `build_tools`)
- Test: `tests/test_tool_defs.py`

- [ ] **Step 1.1: Обновить существующий тест `test_required_minimal` — убрать `month` из required**

Заменить в `tests/test_tool_defs.py`:

```python
def test_required_minimal(register_meta):
    tools = build_tools(register_meta)
    required = tools[0]["function"]["parameters"]["required"]
    assert "mode" in required
    assert "resource" in required
    assert "year" in required
    # month is optional — absence means whole year
    assert "month" not in required
    # Filters should NOT be required — Python applies defaults
    assert "scenario" not in required
    assert "company" not in required
```

- [ ] **Step 1.2: Добавить тест, что фильтр-измерения — массивы**

Добавить в `tests/test_tool_defs.py`:

```python
def test_filter_dims_are_arrays(register_meta):
    """Filter dimensions with allowed_values must be arrays of enum strings."""
    tools = build_tools(register_meta)
    props = tools[0]["function"]["parameters"]["properties"]
    for key in ("scenario", "metric", "company", "contour"):
        assert props[key]["type"] == "array", f"{key} must be array"
        items = props[key]["items"]
        assert items["type"] == "string"
        assert "enum" in items
    # Scenario enum values preserved on items.enum
    assert set(props["scenario"]["items"]["enum"]) == {"Факт", "Прогноз", "План"}


def test_filter_dim_without_allowed_values_is_array_without_enum():
    """A filter dim with no allowed_values is still array<string>, no enum."""
    meta = {
        "name": "РегистрСведений.Тест",
        "dimensions": [
            {"name": "Показатель", "filter_type": "=", "required": True,
             "allowed_values": []},
        ],
        "resources": [{"name": "Сумма"}],
    }
    tools = build_tools(meta)
    props = tools[0]["function"]["parameters"]["properties"]
    assert props["metric"]["type"] == "array"
    assert props["metric"]["items"]["type"] == "string"
    assert "enum" not in props["metric"]["items"]
```

- [ ] **Step 1.3: Запустить тесты, убедиться что падают**

Run: `pytest tests/test_tool_defs.py::test_required_minimal tests/test_tool_defs.py::test_filter_dims_are_arrays tests/test_tool_defs.py::test_filter_dim_without_allowed_values_is_array_without_enum -v`
Expected: FAIL (текущая schema — строки, `month` в required)

- [ ] **Step 1.4: Правка `_filter_properties` в `api/tool_defs.py`**

Заменить тело функции `_filter_properties` (строки 28-73) на:

```python
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
        desc += ". Always pass as array, even for one value."
        if default:
            desc += f" Default: {default}."

        props[key] = {
            "type": "array",
            "items": item_schema,
            "description": desc,
        }

    return props, required
```

- [ ] **Step 1.5: Убрать `month` из `required` в `build_tools`**

В `api/tool_defs.py`, в функции `build_tools`, заменить:

```python
    required = ["mode", "resource", "year", "month"]
```

на:

```python
    required = ["mode", "resource", "year"]
```

И обновить описание поля `month` в `properties`:

```python
        "month": {
            "type": "integer",
            "description": (
                "Month 1-12 (e.g. 'март' = 3). "
                "Omit entirely for whole-year queries ('за 2024 год')."
            ),
        },
```

- [ ] **Step 1.6: Запустить тесты — должны пройти**

Run: `pytest tests/test_tool_defs.py -v`
Expected: PASS (все тесты файла, включая новые)

- [ ] **Step 1.7: Commit**

```bash
git add api/tool_defs.py tests/test_tool_defs.py
git commit -m "feat(schema): filter dims as arrays, month optional"
```

---

## Task 2: Few-shot examples — массивы и year-only

**Files:**
- Modify: `api/tool_defs.py` (функция `build_system_message`, `_format_kwargs`)
- Test: `tests/test_tool_defs.py`

- [ ] **Step 2.1: Добавить тест, что в few-shot есть массив и year-only пример**

Добавить в `tests/test_tool_defs.py`:

```python
def test_system_message_has_array_filter_example(register_meta):
    msg = build_system_message(register_meta)
    # At least one example should pass a filter as an array literal
    # e.g. metric=["Выручка"] or similar
    assert '["' in msg, "Expected array-literal filter in few-shot"


def test_system_message_has_year_only_example(register_meta):
    """A 'whole year' example must appear — year without month."""
    msg = build_system_message(register_meta)
    assert "за 2024 год" in msg or "за 2025 год" in msg
    # And the answer side should not include month=... for that example
    # (we check structurally: the example block contains year= without month=)
    lines = msg.splitlines()
    year_only_q = next((i for i, l in enumerate(lines)
                        if "год" in l and "Q:" in l and "март" not in l and "мая" not in l), None)
    assert year_only_q is not None
    answer = lines[year_only_q + 1]
    assert "year=" in answer
    assert "month=" not in answer
```

- [ ] **Step 2.2: Запустить — падают**

Run: `pytest tests/test_tool_defs.py::test_system_message_has_array_filter_example tests/test_tool_defs.py::test_system_message_has_year_only_example -v`
Expected: FAIL

- [ ] **Step 2.3: Обновить `_format_kwargs`, чтобы массивы рендерились для фильтров**

В `api/tool_defs.py` заменить функцию `_format_kwargs` на версию, которая всегда оборачивает фильтр-значения в массив. Добавить вспомогательный список «ключей фильтров» аргументом:

```python
def _format_kwargs(pairs: list[tuple[str, object]], filter_keys: set[str] | None = None) -> str:
    """Render list of (key, value) as Python-like kwargs for few-shot.

    filter_keys: keys that must be rendered as arrays, wrapping scalars.
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
```

- [ ] **Step 2.4: Обновить `build_system_message` — пробрасывать filter_keys + добавить year-only пример**

В `api/tool_defs.py`, в `build_system_message`, перед блоком «EXAMPLES» собрать набор filter-ключей:

```python
    filter_keys = {_dim_key(d["name"]) for d in register_metadata.get("dimensions", [])
                   if d.get("filter_type") not in ("year_month", "range")
                   and not is_technical_dim(d)}
```

И во всех вызовах `_format_kwargs(...)` внутри функции передавать `filter_keys`:

```python
lines.append(f'A: query({_format_kwargs(agg_kwargs, filter_keys)})')
...
lines.append(f'A: query({_format_kwargs(gb_kwargs, filter_keys)})')
...
lines.append(f'A: query({_format_kwargs(cmp_kwargs, filter_keys)})')
```

Сразу после блока aggregate добавить year-only пример. Вставить после существующего aggregate-блока (перед group_by):

```python
    # year-only example (no month)
    yo_kwargs: list[tuple[str, object]] = [("mode", "aggregate"), ("resource", res)]
    if metric_key and metric_value:
        yo_kwargs.append((metric_key, metric_value))
    yo_kwargs.append(("year", 2024))
    lines.append("")
    lines.append(f'Q: "{topic} за 2024 год"')
    lines.append(f'A: query({_format_kwargs(yo_kwargs, filter_keys)})')
```

Также обновить RULES — добавить пункт про массивы и про пропуск месяца:

```python
    lines.append("6. Filter values are ARRAYS. Pass [\"Выручка\"] for one value, [\"ДЗО-1\",\"ДЗО-2\"] for many.")
    lines.append("7. For whole-year questions ('за 2024 год') omit 'month' entirely.")
```

- [ ] **Step 2.5: Запустить тесты файла**

Run: `pytest tests/test_tool_defs.py -v`
Expected: PASS

- [ ] **Step 2.6: Commit**

```bash
git add api/tool_defs.py tests/test_tool_defs.py
git commit -m "feat(prompt): array filters and year-only example in few-shot"
```

---

## Task 3: `_normalize_params` — толерантность к строке, отбрасывание пустого массива, опциональный month

**Files:**
- Modify: `api/tool_caller.py` (функция `_normalize_params`)
- Test: `tests/test_tool_caller.py`

- [ ] **Step 3.1: Обновить существующий тест `test_normalize_aggregate` под массивы**

Заменить в `tests/test_tool_caller.py` тест `test_normalize_aggregate`:

```python
def test_normalize_aggregate_arrays():
    """Array filter values pass through as arrays; defaults also emitted as arrays."""
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "scenario": ["Факт"],
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert tool == "aggregate"
    assert params["filters"]["Показатель"] == ["Выручка"]
    assert params["filters"]["Сценарий"] == ["Факт"]
    assert params["period"] == {"year": 2025, "month": 3}
```

Удалить старый `test_normalize_aggregate` (строки 36-49).

- [ ] **Step 3.2: Обновить `test_normalize_aggregate_applies_defaults` — defaults тоже как массивы**

Заменить в `tests/test_tool_caller.py`:

```python
def test_normalize_aggregate_applies_defaults():
    """Missing scenario → default 'Факт' from metadata, wrapped in array."""
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert params["filters"]["Сценарий"] == ["Факт"]
    assert params["filters"]["КонтурПоказателя"] == ["свод"]
```

- [ ] **Step 3.3: Добавить новые тесты толерантности**

Добавить в `tests/test_tool_caller.py` после `test_normalize_aggregate_applies_defaults`:

```python
def test_normalize_string_filter_coerced_to_array():
    """Small models sometimes emit a string — wrap in single-element array."""
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": "Выручка",
        "scenario": "Факт",
        "year": 2025, "month": 3,
    }
    _, params = _normalize_params(args, REGISTER_META)
    assert params["filters"]["Показатель"] == ["Выручка"]
    assert params["filters"]["Сценарий"] == ["Факт"]


def test_normalize_empty_array_dropped_and_default_applied():
    """Empty array is dropped; default kicks in as an array if one exists."""
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "scenario": [],  # empty → dropped → default applied
        "year": 2025, "month": 3,
    }
    _, params = _normalize_params(args, REGISTER_META)
    assert params["filters"]["Сценарий"] == ["Факт"]  # default applied


def test_normalize_multi_value_company_preserved():
    """['ДЗО-1','ДЗО-2'] stays as two-element array in filters."""
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "company": ["ДЗО-1", "ДЗО-2"],
        "year": 2025, "month": 3,
    }
    _, params = _normalize_params(args, REGISTER_META)
    assert params["filters"]["ДЗО"] == ["ДЗО-1", "ДЗО-2"]


def test_normalize_year_only_no_month():
    """month absent → period has only 'year', no 'month' key."""
    args = {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "scenario": ["Факт"],
        "company": ["ДЗО-1"],
        "year": 2024,
    }
    _, params = _normalize_params(args, REGISTER_META)
    assert params["period"] == {"year": 2024}
    assert "month" not in params["period"]
    assert params["needs_clarification"] is False


def test_normalize_compare_values_unchanged():
    """compare_values is not a filter — keep list shape, don't touch it."""
    args = {
        "mode": "compare",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "compare_by": "scenario",
        "compare_values": ["Факт", "План"],
        "year": 2025, "month": 3,
    }
    _, params = _normalize_params(args, REGISTER_META)
    assert params["values"] == ["Факт", "План"]
```

Также поправить `test_normalize_compare` (уже существующий) — `metric` должен быть массивом:

```python
def test_normalize_compare():
    args = {
        "mode": "compare",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "compare_by": "scenario",
        "compare_values": ["Факт", "План"],
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert tool == "compare"
    assert params["compare_by"] == "Сценарий"
    assert params["values"] == ["Факт", "План"]
    assert "Сценарий" not in params["filters"]
    assert params["filters"]["Показатель"] == ["Выручка"]
```

И `test_normalize_group_by`:

```python
def test_normalize_group_by():
    args = {
        "mode": "group_by",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "group_by": "company",
        "year": 2025,
        "month": 3,
    }
    tool, params = _normalize_params(args, REGISTER_META)
    assert tool == "group_by"
    assert params["group_by"] == ["ДЗО"]
    assert "ДЗО" not in params["filters"]
    assert params["filters"]["Показатель"] == ["Выручка"]
```

- [ ] **Step 3.4: Запустить тесты — должны падать**

Run: `pytest tests/test_tool_caller.py -v -k "normalize"`
Expected: FAIL (текущая реализация складывает скаляры)

- [ ] **Step 3.5: Реализовать изменения в `_normalize_params`**

В `api/tool_caller.py` заменить тело функции `_normalize_params` (строки 256-353) на:

```python
def _normalize_params(args: dict, register_metadata: dict) -> tuple[str, dict]:
    """Convert single query tool arguments to 1C HTTP service format.

    Args use Latin keys (metric, scenario, company, year, month) + mode.
    Filter values are always arrays in the 1C payload. Strings are wrapped
    in single-element arrays; empty arrays are dropped so that defaults can
    apply.

    Returns (tool_name, params) where tool_name is the mode value
    and params use 1C names (Показатель, Сценарий, ДЗО) + period dict.
    """
    mode = args.get("mode", "aggregate")
    resource = args.get("resource", "Сумма")
    year = args.get("year")
    month = args.get("month")
    group_by_latin = args.get("group_by")
    order_by = args.get("order", args.get("order_by", "desc"))
    limit = args.get("limit", 1000)

    # Period: month is optional; absence == whole year
    period: dict = {}
    if year is not None:
        period["year"] = year
        if month is not None:
            period["month"] = month

    compare_by_cyrillic = key_to_dim(args.get("compare_by", "")) if mode == "compare" else ""
    group_by_cyrillic = key_to_dim(group_by_latin) if group_by_latin else ""

    skip_keys = {
        "mode", "resource", "year", "month", "group_by", "order", "order_by",
        "limit", "compare_by", "compare_values",
    }

    def _coerce_filter_value(value) -> list | None:
        """Normalize a filter value to a list of strings, or None to drop it."""
        if value is None:
            return None
        if isinstance(value, list):
            cleaned = [str(v) for v in value if v is not None and str(v) != ""]
            return cleaned or None
        # Scalar — wrap in a single-element list
        s = str(value)
        return [s] if s else None

    filters: dict = {}
    for k, v in args.items():
        if k in skip_keys:
            continue
        dim_name = key_to_dim(k)
        # Exclude dimension used for group_by or compare_by from filters
        if dim_name == group_by_cyrillic:
            continue
        if dim_name == compare_by_cyrillic:
            continue
        coerced = _coerce_filter_value(v)
        if coerced is None:
            continue
        filters[dim_name] = coerced

    # Apply defaults for dimensions not provided (defaults go in as arrays too)
    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        if dim.get("filter_type") in ("year_month", "range"):
            continue
        if name == group_by_cyrillic or name == compare_by_cyrillic:
            continue
        if name not in filters and dim.get("default_value"):
            filters[name] = [str(dim["default_value"])]

    group_by: list = []
    if group_by_latin:
        group_by = [group_by_cyrillic]

    extra: dict = {}
    if mode == "compare":
        extra["compare_by"] = compare_by_cyrillic
        extra["values"] = args.get("compare_values", [])

    # needs_clarification: only the non-month required dims matter (month optional)
    needs_clarification = False
    for dim in register_metadata.get("dimensions", []):
        name = dim["name"]
        if is_technical_dim(dim):
            continue
        if not dim.get("required"):
            continue
        if dim.get("default_value"):
            continue
        ft = dim.get("filter_type", "=")
        if ft in ("year_month", "range"):
            if not period.get("year"):
                needs_clarification = True
                break
        elif ft == "=":
            if mode == "compare" and name == compare_by_cyrillic:
                continue
            if name in group_by:
                continue
            if name not in filters:
                needs_clarification = True
                break

    result = {
        "resource": resource,
        "filters": filters,
        "period": period,
        "group_by": group_by,
        "order_by": order_by,
        "limit": limit,
        "needs_clarification": needs_clarification,
    }
    result.update(extra)
    return mode, result
```

- [ ] **Step 3.6: Запустить тесты — должны пройти**

Run: `pytest tests/test_tool_caller.py -v`
Expected: PASS (все тесты файла)

- [ ] **Step 3.7: Commit**

```bash
git add api/tool_caller.py tests/test_tool_caller.py
git commit -m "feat(normalize): arrays for filters, optional month"
```

---

## Task 4: `param_validator` — массивы filters, month опционален

**Files:**
- Modify: `api/param_validator.py`
- Test: `tests/test_param_validator.py`

- [ ] **Step 4.1: Прочитать текущий тест**

Run: `cat tests/test_param_validator.py | head -80`
Нужно увидеть текущие фикстуры, чтобы не ломать их имена.

- [ ] **Step 4.2: Добавить тесты для массивов**

Добавить в `tests/test_param_validator.py`:

```python
def test_validate_filter_as_list_ok(register_meta):
    """Filter values as a list of canonical strings pass validation."""
    from api.param_validator import validate
    tr = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {"ДЗО": ["ДЗО-1", "ДЗО-2"]},
            "period": {"year": 2024},
        },
    }
    result = validate(tr, register_meta)
    assert result.ok, result.errors


def test_validate_filter_list_fuzzy_resolved(register_meta):
    """Each element of a filter list is fuzzy-resolved to canonical form."""
    from api.param_validator import validate
    tr = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {"ДЗО": ["дзо-1", "ДЗО-2"]},
            "period": {"year": 2024},
        },
    }
    result = validate(tr, register_meta)
    assert result.ok, result.errors
    assert tr["params"]["filters"]["ДЗО"] == ["ДЗО-1", "ДЗО-2"]


def test_validate_filter_list_one_invalid_element_errors(register_meta):
    """An unresolvable element in a filter list produces a single error
    pointing at the offending index."""
    from api.param_validator import validate
    tr = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {"ДЗО": ["ДЗО-1", "НЕСУЩЕСТВУЮЩЕЕ"]},
            "period": {"year": 2024},
        },
    }
    result = validate(tr, register_meta)
    assert not result.ok
    joined = " | ".join(result.errors)
    assert "ДЗО" in joined and "НЕСУЩЕСТВУЮЩЕЕ" in joined


def test_validate_year_only_period_ok(register_meta):
    """period {year: 2024} without 'month' passes validation."""
    from api.param_validator import validate
    tr = {
        "tool": "aggregate",
        "params": {
            "resource": "Сумма",
            "filters": {"Показатель": ["Выручка"], "ДЗО": ["ДЗО-1"]},
            "period": {"year": 2024},
        },
    }
    result = validate(tr, register_meta)
    assert result.ok, result.errors
```

Фикстуру `register_meta` в этом файле тоже нужно проверить. Если её нет — добавить в начало файла (скопировать из `tests/test_tool_defs.py`).

- [ ] **Step 4.3: Запустить — падают**

Run: `pytest tests/test_param_validator.py -v -k "filter_as_list or filter_list or year_only"`
Expected: FAIL

- [ ] **Step 4.4: Обновить `validate` в `api/param_validator.py`**

В секции «Filter values check» (строки 114-133) заменить на версию, обрабатывающую список:

```python
    # Filter values check (each value is expected as a list; scalars tolerated
    # for backwards compatibility but should normally be coerced upstream).
    filters = params.get("filters", {})
    dims_by_name = {d["name"]: d for d in register_metadata.get("dimensions", [])}
    for dim_name, value in list(filters.items()):
        if value is None:
            continue
        dim = dims_by_name.get(dim_name)
        if not dim:
            continue
        allowed = dim.get("allowed_values") or []

        # Accept a scalar and keep it as-is if allowed is empty — nothing to resolve.
        items = value if isinstance(value, list) else [value]
        if not allowed:
            continue

        resolved: list[str] = []
        had_error = False
        for idx, item in enumerate(items):
            canonical, candidates = _resolve_enum(item, allowed)
            if canonical is not None:
                resolved.append(canonical)
            else:
                hint = candidates or allowed
                errors.append(
                    f'{dim_name}[{idx}]: copy EXACTLY one of {hint}. '
                    f'You wrote "{item}".'
                )
                had_error = True

        if not had_error:
            # Write back as a list (canonicalized)
            filters[dim_name] = resolved if isinstance(value, list) else resolved
```

Также убрать требование `month` из period-check (строки 106-112) — оставить только если задан:

```python
    # Period check
    period = params.get("period", {})
    year = period.get("year")
    month = period.get("month")
    if year is not None and not (YEAR_MIN <= year <= YEAR_MAX):
        errors.append(f"year: must be an integer between {YEAR_MIN} and {YEAR_MAX}, not {year}.")
    if month is not None and not (1 <= month <= 12):
        errors.append(f"month: must be an integer between 1 and 12, not {month}.")
```

(логика та же, оставляем — `month is None` уже корректно пропускает проверку)

- [ ] **Step 4.5: Запустить все тесты валидатора**

Run: `pytest tests/test_param_validator.py -v`
Expected: PASS

- [ ] **Step 4.6: Commit**

```bash
git add api/param_validator.py tests/test_param_validator.py
git commit -m "feat(validator): array filter values, optional month"
```

---

## Task 5: BSL — `В (&Знач)` для фильтров, опциональный месяц

**Files:**
- Modify: `docs/1c-http-service-module.md` (BSL код модуля)
- Modify: `docs/1c-http-service-spec.md` (текст контракта)

Это документационные файлы (исходник BSL-модуля, который разработчик вставляет в Конфигуратор). Код в 1С правит пользователь вручную — мы обновляем эталон.

- [ ] **Step 5.1: Обновить раздел `ПостроитьУсловияОтбора` в BSL-модуле**

В `docs/1c-http-service-module.md` найти процедуру `ПостроитьУсловияОтбора` и заменить её на версию, где filters всегда массив и период без месяца обрабатывается:

```bsl
Функция ПостроитьУсловияОтбора(Запрос, Фильтры, Период, ИмяИзмеренияДаты)
    УсловияМассив = Новый Массив;

    Для Каждого Эл Из Фильтры Цикл
        ИмяИзм = Эл.Ключ;
        Знач = Эл.Значение;

        // Контракт: filters[dim] — всегда массив строк.
        // Принимаем также скаляр для обратной совместимости.
        МассивЗначений = Новый Массив;
        Если ТипЗнч(Знач) = Тип("Массив") Тогда
            Для Каждого В Из Знач Цикл
                МассивЗначений.Добавить(В);
            КонецЦикла;
        Иначе
            МассивЗначений.Добавить(Знач);
        КонецЕсли;

        Если МассивЗначений.Количество() = 0 Тогда
            Продолжить;
        КонецЕсли;

        ИмяПараметра = "П_" + ИмяИзм;
        УсловияМассив.Добавить(ИмяИзм + " В (&" + ИмяПараметра + ")");
        Запрос.УстановитьПараметр(ИмяПараметра, МассивЗначений);
    КонецЦикла;

    Если Период <> Неопределено Тогда
        Год = Период.Получить("year");
        Месяц = Период.Получить("month");
        Если Год <> Неопределено Тогда
            УсловияМассив.Добавить("ГОД(" + ИмяИзмеренияДаты + ") = &Год");
            Запрос.УстановитьПараметр("Год", Год);
        КонецЕсли;
        Если Месяц <> Неопределено Тогда
            УсловияМассив.Добавить("МЕСЯЦ(" + ИмяИзмеренияДаты + ") = &Месяц");
            Запрос.УстановитьПараметр("Месяц", Месяц);
        КонецЕсли;
    КонецЕсли;

    Если УсловияМассив.Количество() = 0 Тогда
        Возврат "";
    КонецЕсли;
    Возврат "ГДЕ " + СтрСоединить(УсловияМассив, " И ");
КонецФункции
```

Если процедура выглядит иначе в текущем файле — привести к этой форме, сохранив стиль существующего модуля (используемые имена переменных, комментарии).

- [ ] **Step 5.2: Обновить пример payload в спеке**

В `docs/1c-http-service-spec.md` заменить пример запроса на:

```json
{
  "register": "Витрина_Дашборда",
  "tool": "aggregate",
  "params": {
    "resource": "Сумма",
    "filters": {
      "Сценарий": ["Факт"],
      "КонтурПоказателя": ["свод"],
      "Показатель": ["Выручка"],
      "ДЗО": ["ДЗО-1", "ДЗО-2"]
    },
    "period": {
      "year": 2024
    }
  }
}
```

Добавить подсекцию «Фильтры — всегда массив»:

```markdown
### Фильтры

Каждое значение `filters[<измерение>]` — массив строк. Одно значение —
`["Факт"]`, несколько — `["ДЗО-1","ДЗО-2"]`. Для одноэлементного массива
1С строит `<имя> В (&Знач)` — семантически эквивалентно `= &Знач`.

### Период

`period.year` обязателен. `period.month` опционален: если отсутствует,
условие по периоду сводится к `ГОД(<date_dim>) = &Год` — запрос за весь год.
```

- [ ] **Step 5.3: Обновить шаблоны SQL в спеке**

В секции «Обработчики по инструментам» → `aggregate`, `group_by`, `top_n`, `filtered` заменить псевдо-условие в `ГДЕ` на:

```
ГДЕ
    <условия из filters (через В(...)) + period>
```

(остальные шаблоны уже используют `ПостроитьУсловияОтбора`, менять не нужно).

- [ ] **Step 5.4: Commit**

```bash
git add docs/1c-http-service-module.md docs/1c-http-service-spec.md
git commit -m "docs(1c): array filters and optional month in /analytics_execute"
```

---

## Task 6: `registers.example.yaml` — имя без префикса типа

**Files:**
- Modify: `registers.example.yaml`

- [ ] **Step 6.1: Убрать префикс из имени регистра**

В `registers.example.yaml` заменить:

```yaml
  - name: РегистрСведений.Витрина_Дашборда
    description: Витрина дашборда
    type: information_register
```

на:

```yaml
  - name: Витрина_Дашборда
    description: Витрина дашборда
    type: information_register   # РегистрСведений.<name> в 1С собирается из type + name
```

- [ ] **Step 6.2: Commit**

```bash
git add registers.example.yaml
git commit -m "docs(yaml): register name without type prefix"
```

---

## Task 7: CLAUDE.md — раздел «Adding a new register» + caveat

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 7.1: Добавить раздел**

В `CLAUDE.md`, после секции «Dimension Key Mapping (tool_defs.py)», вставить новый раздел:

```markdown
## Adding a new register

Чек-лист при подключении нового регистра из 1С:

1. **Имя регистра в `registers.yaml`** — только идентификатор
   (`Витрина_Выручка`), **без** префикса типа. Префикс (`РегистрСведений.`
   / `РегистрНакопления.`) 1С восстанавливает из поля `type`. Префикс в
   `name` ломает маршрутизацию на стороне 1С.

2. **Latin-ключи для каждого нового измерения.** Добавить в
   `_KEY_TO_DIM` и `_dim_key()` (`api/tool_defs.py`) маппинг
   `<Русское имя> ↔ <latin_key>`. Пропущенный маппинг = кириллица в JSON
   Schema = сломанный tool calling у небольших моделей.

3. **Вручную проверить `technical` и `default`** в `registers.yaml`:
   - `technical: true` — поле скрывается от модели (вспомогательные
     измерения вроде `Масштаб`, `Ед_изм`, `Показатель_номер`). Если не
     разметить — модель пытается их заполнять и получает уточнения.
   - `default: <значение>` — подставляется автоматически в `filters`,
     если пользователь не указал. Отсутствие дефолта у required dim
     заставляет бэкенд переспрашивать.
   Альтернатива ручной правке — запустить интервью:
   `python3 scripts/sync_metadata.py` (пройти по dimensions).

4. **Проверить `.env`** перед калибровкой:
   ```bash
   cat .env | grep MODEL_NAME
   ```
   Модель, которой вы проверяете tool calling, может не совпадать с
   тем, что написано в CLAUDE.md (пример из истории проекта).

5. **Запустить** `python3 scripts/seed_metadata.py` → перезапустить
   `uvicorn` → реальный вопрос в браузере.
```

- [ ] **Step 7.2: Добавить caveat в «Lessons learned»**

В CLAUDE.md, в подраздел «SLM tool calling», в конец списка добавить:

```markdown
- Один tool (`query` с `mode` enum) маскирует ошибки выбора функции — даже Qwen3.5:2b не промахивается, потому что промахнуться некуда. При появлении 2+ tools обязательна повторная калибровка: без неё проблема проявится на проде
```

- [ ] **Step 7.3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: adding-a-new-register checklist and single-tool caveat"
```

---

## Task 8: Calibration — новые кейсы

**Files:**
- Modify: `scripts/calibrate_tools.py`

- [ ] **Step 8.1: Прочитать текущую структуру**

Run: `grep -n "case\|Case\|CASES\|Вопрос\|question" scripts/calibrate_tools.py | head -40`

Посмотреть, как добавляются кейсы (обычно — список словарей или dataclass).

- [ ] **Step 8.2: Добавить два новых кейса**

В `scripts/calibrate_tools.py`, в списке кейсов добавить (подстройтесь под формат существующих записей — дословная структура зависит от того, как они оформлены):

```python
{
    "question": "Выручка за 2024 год",
    "expected": {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "year": 2024,
        # month intentionally absent — whole-year query
    },
    "comment": "Year-only period",
},
{
    "question": "Выручка за 2024 у ДЗО-1 и ДЗО-2",
    "expected": {
        "mode": "aggregate",
        "resource": "Сумма",
        "metric": ["Выручка"],
        "company": ["ДЗО-1", "ДЗО-2"],
        "year": 2024,
    },
    "comment": "Multi-value company filter",
},
```

Если проверка в скрипте использует точное равенство — возможно, нужно скорректировать matcher, чтобы отсутствие `month` не считалось ошибкой (проверить по коду `calibrate_tools.py` в Step 8.1).

- [ ] **Step 8.3: Запустить калибровку локально**

Run: `python3 scripts/calibrate_tools.py -v`
Expected: новые кейсы проходят (или показывают реальные проценты для модели — цель не 100%, а видимость регрессии).

Если модель ломается — это ожидаемо, фиксируем baseline. Коммит всё равно делаем.

- [ ] **Step 8.4: Commit**

```bash
git add scripts/calibrate_tools.py
git commit -m "test(calibration): year-only and multi-value cases"
```

---

## Task 9: E2E / тесты калибровочных кейсов (если файл используется в CI)

**Files:**
- Modify: `tests/test_calibration_cases.py` (если в нём ссылаются на кейсы из скрипта)

- [ ] **Step 9.1: Проверить, нужен ли этот шаг**

Run: `grep -n "year\|month\|company\|ДЗО" tests/test_calibration_cases.py | head -30`

Если тесты статичные (не читают из `calibrate_tools.py`) и уже были зелёные — никаких правок.

- [ ] **Step 9.2: При необходимости — добавить кейсы туда же**

Если тесты дублируют expected-структуру, добавить для них такой же кейс «year-only» и «multi-value company», используя новый контракт (`metric: ["Выручка"]`, `company: ["ДЗО-1","ДЗО-2"]`).

- [ ] **Step 9.3: Запустить весь pytest**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 9.4: Commit (если что-то поменяли)**

```bash
git add tests/test_calibration_cases.py
git commit -m "test: calibration cases use new array/year-only contract"
```

---

## Task 10: Полный прогон + ручной smoke-тест

Правило из CLAUDE.md: «CI-зелёный ≠ работает в браузере».

- [ ] **Step 10.1: Полный pytest**

Run: `pytest tests/ -v`
Expected: все тесты зелёные.

- [ ] **Step 10.2: Рестарт uvicorn**

Run: `uvicorn api.main:app --reload --port 8000`
Фоновый процесс.

- [ ] **Step 10.3: Ручная проверка в браузере**

Задать три вопроса в веб-интерфейсе:

1. «Выручка за 2024 год» — должен прийти ответ со всеми 12 месяцами (или годовая сумма, как отдаёт 1С).
2. «Выручка за 2024 у ДЗО-1 и ДЗО-2» — ответ агрегированный по двум ДЗО (или суммарный, в зависимости от обработчика 1С).
3. «Какая выручка за март 2024?» — регрессия, должно работать как раньше.

- [ ] **Step 10.4: При расхождении**

Если 1С возвращает ошибку — проверить `docs/1c-http-service-module.md`, перенесён ли `ПостроитьУсловияОтбора` в живой модуль Конфигуратора. Правило: эталон в доке = код в 1С.

- [ ] **Step 10.5: Финальный commit (если были правки после smoke)**

```bash
git add -A
git commit -m "fix: polish after manual smoke test"
```

---

## Self-review

**Spec coverage:**
- Бaг 1 (массивы фильтров) → Task 1, 3, 4, 5.
- Баг 2 (year-only) → Task 1, 2, 3, 4, 5.
- Баг 3 (префикс в имени) → Task 6 (+ обновление спеки в Task 5.2).
- Docs «Adding a new register» → Task 7.
- Caveat «один tool» → Task 7.
- Тесты и калибровка → Tasks 1-4, 8-10.

**Placeholder scan:** коды конкретные, test-блоки содержат assertions, commands конкретные.

**Type consistency:**
- `filters[<dim>]` всегда `list[str]` во всех тасках.
- `period` — `{"year": int}` или `{"year": int, "month": int}`.
- BSL-параметры: `П_<ИмяИзм>` — массив 1С.

Нет несоответствий.
