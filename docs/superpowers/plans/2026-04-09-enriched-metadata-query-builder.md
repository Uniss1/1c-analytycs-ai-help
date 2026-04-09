# Enriched Metadata & Template-Based Query Builder

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-form query building with template-based generation that uses enriched register metadata (required filters, allowed values, date function types) so every generated query includes all mandatory WHERE conditions with validated values.

**Architecture:** `sync_metadata` probes 1C for each dimension's distinct values and writes enriched YAML (required/values/default/filter_type per dimension + query_template per register). `query_builder` fills in the template from LLM-extracted params. `param_extractor` prompt shows allowed values so LLM picks from a list. Validation rejects params with values not in the allowed list or missing required filters.

**Tech Stack:** Python 3.12, FastAPI, SQLite, PyYAML, pytest

---

### Task 1: Enriched registers.yaml format

**Files:**
- Modify: `registers.yaml`

This task defines the target YAML schema. All subsequent tasks build toward generating and consuming this format.

- [ ] **Step 1: Replace registers.yaml with enriched example**

```yaml
# Регистры 1С Аналитики
# Добавьте имена регистров, затем запустите:
#   python3 scripts/sync_metadata.py   — заполнит из 1С (dimensions, resources, keywords)
#   python3 scripts/seed_metadata.py   — загрузит в metadata.db

registers:
  - name: РегистрСведений.Витрина_Дашборда
    description: Витрина дашборда
    type: information_register

    dimensions:
      - name: Сценарий
        data_type: Строка
        required: true
        default: "Факт"
        values: ["Факт", "Прогноз", "План"]

      - name: КонтурПоказателя
        data_type: Строка
        required: true
        default: "свод"
        values: ["свод", "детализация"]

      - name: Показатель
        data_type: Строка
        required: true
        default: null
        values: ["Выручка", "ОЗП", "Маржа", "EBITDA"]

      - name: ДЗО
        data_type: Строка
        required: true
        default: null
        values: ["Консолидация", "ДЗО-1", "ДЗО-2"]

      - name: Период_Показателя
        data_type: Дата
        required: true
        default: null
        filter_type: "year_month"
        # filter_type values:
        #   "=" — simple equality: Поле = &Param
        #   "range" — date range: Поле >= &Начало И Поле <= &Конец
        #   "year_month" — ГОД(Поле) = &Год И МЕСЯЦ(Поле) = &Месяц

      - name: Масштаб
        data_type: Строка
        required: false
        values: ["тыс.", "млн."]

    resources:
      - name: Сумма

    keywords:
      - выручка
      - дашборд
      - витрина

    query_template: |
      ВЫБРАТЬ ПЕРВЫЕ {limit}
          {select}
      ИЗ
          {register}
      ГДЕ
          {where}
      {group_by}
      {order_by}
```

- [ ] **Step 2: Commit**

```bash
git add registers.yaml
git commit -m "feat: define enriched registers.yaml schema with required/values/filter_type"
```

---

### Task 2: Enrich sync_metadata to discover values and required fields

**Files:**
- Modify: `scripts/sync_metadata.py`

- [ ] **Step 1: Write the enriched dimension discovery**

Replace `classify_fields` and the main sync loop to produce enriched dimensions. Key changes:

In `sync_metadata.py`, replace the `synced[name]` dict construction (around line 245) and the `classify_fields` function. The new flow:

1. Probe register with `ВЫБРАТЬ ПЕРВЫЕ 1 * ИЗ register` — get field names and types
2. For EVERY string/enum dimension (not just DIMENSION_KEYWORDS_FIELDS): get distinct values via `ВЫБРАТЬ РАЗЛИЧНЫЕ ПЕРВЫЕ 500 field ИЗ register`
3. Mark dimensions as `required: true` if they have fewer than 50 distinct values (categorical = mandatory filter)
4. Date fields get `filter_type: "year_month"` by default
5. Set `default` for known fields: Сценарий → "Факт", КонтурПоказателя → "свод"

Replace the `classify_fields` function:

```python
# Known defaults for common dimensions
KNOWN_DEFAULTS = {
    "Сценарий": "Факт",
    "КонтурПоказателя": "свод",
}

# Max distinct values for a dimension to be considered required (categorical)
MAX_REQUIRED_VALUES = 50


def classify_fields_enriched(
    register_name: str, sample_row: dict
) -> tuple[list[dict], list[dict]]:
    """Classify fields into enriched dimensions and resources."""
    dimensions = []
    resources = []

    for field_name, value in sample_row.items():
        if field_name in SKIP_FIELDS:
            continue

        if isinstance(value, (int, float)) and field_name in KNOWN_RESOURCE_NAMES:
            resources.append({"name": field_name})
            continue

        # Date field
        if isinstance(value, str) and "T" in value and len(value) >= 19:
            dimensions.append({
                "name": field_name,
                "data_type": "Дата",
                "required": True,
                "default": None,
                "filter_type": "year_month",
            })
            continue

        # Numeric non-resource (Месяц, Код, etc.)
        if isinstance(value, (int, float)):
            if any(kw in field_name.lower() for kw in ("месяц", "номер", "код")):
                dimensions.append({
                    "name": field_name,
                    "data_type": "Число",
                    "required": False,
                    "default": None,
                    "filter_type": "=",
                })
            else:
                resources.append({"name": field_name})
            continue

        # String dimension — get distinct values
        values = get_distinct_values(register_name, field_name)
        is_required = 0 < len(values) <= MAX_REQUIRED_VALUES
        default = KNOWN_DEFAULTS.get(field_name)

        dim = {
            "name": field_name,
            "data_type": "Строка",
            "required": is_required,
            "default": default,
            "filter_type": "=",
        }
        if values:
            dim["values"] = values

        dimensions.append(dim)

    return dimensions, resources
```

Replace the synced dict construction in `main()`:

```python
        dimensions, resources = classify_fields_enriched(name, sample)
        print(f"    Измерения: {[d['name'] for d in dimensions]}")
        print(f"    Ресурсы:   {[r['name'] for r in resources]}")

        # Show required dims
        required = [d for d in dimensions if d.get("required")]
        print(f"    Обязательные: {[d['name'] for d in required]}")
        for d in required:
            vals = d.get("values", [])
            if vals:
                print(f"      {d['name']}: {vals[:10]}{'...' if len(vals) > 10 else ''}")

        # Generate keywords from distinct values and register name
        distinct = {}
        for dim in dimensions:
            if dim.get("values") and dim["name"] in DIMENSION_KEYWORDS_FIELDS:
                distinct[dim["name"]] = dim["values"]

        existing_reg = next((r for r in yaml_data.get("registers", []) if isinstance(r, dict) and r.get("name") == name), None)
        existing_kw = existing_reg.get("keywords", []) if existing_reg else []
        keywords = generate_keywords(name, distinct, existing_kw)

        synced[name] = {
            "dimensions": dimensions,
            "resources": resources,
            "keywords": keywords,
        }
```

Also update `update_yaml` to preserve the enriched format — it already does since it replaces dimensions/resources wholesale.

- [ ] **Step 2: Run sync_metadata against 1C to verify output**

```bash
python3 scripts/sync_metadata.py
cat registers.yaml
```

Expected: YAML now has `required`, `values`, `default`, `filter_type` for each dimension.

- [ ] **Step 3: Commit**

```bash
git add scripts/sync_metadata.py
git commit -m "feat: sync_metadata discovers required fields, allowed values, filter_type"
```

---

### Task 3: Update seed_metadata and DB schema for enriched dimensions

**Files:**
- Modify: `scripts/seed_metadata.py`
- Modify: `api/metadata.py`

The DB dimensions table needs new columns: `required`, `default_value`, `filter_type`, `allowed_values` (JSON string).

- [ ] **Step 1: Update create_schema in seed_metadata.py**

Add columns to the dimensions table:

```python
        CREATE TABLE IF NOT EXISTS dimensions (
            id            INTEGER PRIMARY KEY,
            register_id   INTEGER NOT NULL REFERENCES registers(id),
            name          TEXT NOT NULL,
            data_type     TEXT NOT NULL,
            description   TEXT,
            required      INTEGER NOT NULL DEFAULT 0,
            default_value TEXT,
            filter_type   TEXT NOT NULL DEFAULT '=',
            allowed_values TEXT
        );
```

- [ ] **Step 2: Update seed_from_yaml to write enriched fields**

Replace the dimension INSERT in `seed_from_yaml`:

```python
        for dim in reg.get("dimensions", []):
            allowed = json.dumps(dim.get("values", []), ensure_ascii=False) if dim.get("values") else None
            cur.execute(
                """INSERT INTO dimensions
                   (register_id, name, data_type, description, required, default_value, filter_type, allowed_values)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (reg_id, dim["name"], dim.get("data_type", "Строка"), dim.get("description"),
                 1 if dim.get("required") else 0,
                 dim.get("default"),
                 dim.get("filter_type", "="),
                 allowed),
            )
```

Add `import json` at the top of seed_metadata.py.

- [ ] **Step 3: Update _enrich_register in metadata.py**

Update `_enrich_register` to return enriched dimension data:

```python
def _enrich_register(row: sqlite3.Row) -> dict:
    """Add dimensions and resources to a register row."""
    conn = _get_conn()
    reg_id = row["id"]
    dims = conn.execute(
        "SELECT name, data_type, description, required, default_value, filter_type, allowed_values FROM dimensions WHERE register_id = ?",
        (reg_id,),
    ).fetchall()
    ress = conn.execute(
        "SELECT name, data_type, description FROM resources WHERE register_id = ?",
        (reg_id,),
    ).fetchall()

    dimensions = []
    for d in dims:
        dim = dict(d)
        dim["required"] = bool(dim["required"])
        if dim["allowed_values"]:
            dim["allowed_values"] = json.loads(dim["allowed_values"])
        else:
            dim["allowed_values"] = []
        dimensions.append(dim)

    return {
        "name": row["name"],
        "description": row["description"],
        "register_type": row["register_type"],
        "dimensions": dimensions,
        "resources": [dict(r) for r in ress],
    }
```

Add `import json` at the top of metadata.py.

- [ ] **Step 4: Delete old metadata.db and re-seed**

```bash
rm -f metadata.db
python3 scripts/seed_metadata.py
```

- [ ] **Step 5: Run all tests, fix any that break due to new columns**

```bash
python3 -m pytest tests/ -v
```

Update test fixtures (in test_metadata.py, test_chat_e2e.py, test_query_builder.py, test_param_extractor.py) to include the new dimension fields (`required`, `default_value`, `filter_type`, `allowed_values`) in their inline test data.

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_metadata.py api/metadata.py tests/
git commit -m "feat: DB schema stores required/default/filter_type/allowed_values per dimension"
```

---

### Task 4: Rewrite query_builder for template-based generation

**Files:**
- Modify: `api/query_builder.py`
- Modify: `tests/test_query_builder.py`

The new query_builder uses enriched metadata to:
1. Always include all `required` dimensions in WHERE
2. Use `ГОД(field) = &Год И МЕСЯЦ(field) = &Месяц` for `filter_type: "year_month"`
3. Use `field >= &Начало И field <= &Конец` for `filter_type: "range"`
4. Use `field = &Param` for `filter_type: "="`
5. Fill defaults for required dimensions not specified by user

- [ ] **Step 1: Write failing tests for the new query_builder**

Replace `tests/test_query_builder.py`:

```python
"""Tests for query_builder — enriched metadata → 1C query."""

import pytest

from api.query_builder import build_query


@pytest.fixture()
def register_meta():
    """Enriched register metadata matching real 1C structure."""
    return {
        "name": "РегистрСведений.Витрина_Дашборда",
        "description": "Витрина дашборда",
        "register_type": "information_register",
        "dimensions": [
            {"name": "Сценарий", "data_type": "Строка", "required": True,
             "default_value": "Факт", "filter_type": "=",
             "allowed_values": ["Факт", "Прогноз", "План"]},
            {"name": "КонтурПоказателя", "data_type": "Строка", "required": True,
             "default_value": "свод", "filter_type": "=",
             "allowed_values": ["свод", "детализация"]},
            {"name": "Показатель", "data_type": "Строка", "required": True,
             "default_value": None, "filter_type": "=",
             "allowed_values": ["Выручка", "ОЗП", "Маржа"]},
            {"name": "ДЗО", "data_type": "Строка", "required": True,
             "default_value": None, "filter_type": "=",
             "allowed_values": ["Консолидация", "ДЗО-1", "ДЗО-2"]},
            {"name": "Период_Показателя", "data_type": "Дата", "required": True,
             "default_value": None, "filter_type": "year_month",
             "allowed_values": []},
            {"name": "Масштаб", "data_type": "Строка", "required": False,
             "default_value": None, "filter_type": "=",
             "allowed_values": ["тыс.", "млн."]},
        ],
        "resources": [
            {"name": "Сумма", "data_type": "Число"},
        ],
    }


def test_all_required_filters_present(register_meta):
    """All required dimensions appear in WHERE, even with defaults."""
    params = {
        "resource": "Сумма",
        "filters": {
            "Показатель": "Выручка",
            "ДЗО": "Консолидация",
        },
        "period": {"year": 2025, "month": 3},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert "Сценарий = &Сценарий" in result["query"]
    assert result["params"]["Сценарий"] == "Факт"  # default
    assert "КонтурПоказателя = &КонтурПоказателя" in result["query"]
    assert result["params"]["КонтурПоказателя"] == "свод"  # default
    assert "Показатель = &Показатель" in result["query"]
    assert result["params"]["Показатель"] == "Выручка"
    assert "ДЗО = &ДЗО" in result["query"]
    assert "ГОД(Период_Показателя) = &Год" in result["query"]
    assert "МЕСЯЦ(Период_Показателя) = &Месяц" in result["query"]
    assert result["params"]["Год"] == 2025
    assert result["params"]["Месяц"] == 3


def test_year_month_filter(register_meta):
    """Date dimension uses ГОД() and МЕСЯЦ() functions."""
    params = {
        "resource": "Сумма",
        "filters": {"Показатель": "ОЗП", "ДЗО": "ДЗО-1"},
        "period": {"year": 2025, "month": 6},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert "ГОД(Период_Показателя) = &Год" in result["query"]
    assert "МЕСЯЦ(Период_Показателя) = &Месяц" in result["query"]
    # No >= / <= range style
    assert ">=" not in result["query"]
    assert "<=" not in result["query"]


def test_group_by(register_meta):
    """Group by a dimension."""
    params = {
        "resource": "Сумма",
        "filters": {"Показатель": "Выручка"},
        "period": {"year": 2025, "month": 3},
        "group_by": ["ДЗО"],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert "ДЗО," in result["query"]
    assert "СГРУППИРОВАТЬ ПО ДЗО" in result["query"]
    # ДЗО should NOT be in WHERE since it's in GROUP BY
    where_section = result["query"].split("ГДЕ")[1].split("СГРУППИРОВАТЬ")[0]
    assert "ДЗО = &ДЗО" not in where_section


def test_optional_filter_not_forced(register_meta):
    """Optional dimension (Масштаб) not added to WHERE if not provided."""
    params = {
        "resource": "Сумма",
        "filters": {"Показатель": "Выручка", "ДЗО": "Консолидация"},
        "period": {"year": 2025, "month": 1},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert "Масштаб" not in result["query"]


def test_missing_required_returns_error(register_meta):
    """Missing required filter with no default → error dict."""
    params = {
        "resource": "Сумма",
        "filters": {"ДЗО": "Консолидация"},
        # Показатель is required, has no default, not provided
        "period": {"year": 2025, "month": 3},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert "missing_required" in result
    assert "Показатель" in result["missing_required"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_query_builder.py -v
```

Expected: all 5 new tests FAIL.

- [ ] **Step 3: Rewrite query_builder.py**

```python
"""Deterministic query builder: enriched metadata + params → 1C query.

Uses register metadata (required/default/filter_type/allowed_values)
to build correct queries with all mandatory WHERE conditions.
"""

import logging

logger = logging.getLogger(__name__)


def build_query(params: dict, register_metadata: dict) -> dict:
    """Build 1C query from structured parameters + enriched metadata.

    Args:
        params: from param_extractor (resource, filters, period, group_by, etc.)
        register_metadata: enriched metadata with required/values/filter_type per dimension

    Returns:
        {"query": str, "params": dict}
        OR {"query": None, "missing_required": [...], "params": {}}
    """
    register_name = register_metadata["name"]
    resource = params.get("resource", "Сумма")
    group_by = params.get("group_by", [])
    order_by = params.get("order_by", "desc")
    limit = params.get("limit", 1000)
    filters = params.get("filters", {})
    period = params.get("period", {})
    group_by_set = set(group_by)

    dimensions = register_metadata.get("dimensions", [])

    conditions = []
    query_params = {}
    missing_required = []

    for dim in dimensions:
        dim_name = dim["name"]
        required = dim.get("required", False)
        default = dim.get("default_value") or dim.get("default")
        filter_type = dim.get("filter_type", "=")

        # Skip dimensions that are in GROUP BY — they go in SELECT, not WHERE
        if dim_name in group_by_set:
            continue

        # Resolve value: user-provided > default
        value = filters.get(dim_name)

        if filter_type == "year_month":
            year = period.get("year")
            month = period.get("month")
            if year and month:
                conditions.append(f"ГОД({dim_name}) = &Год")
                conditions.append(f"МЕСЯЦ({dim_name}) = &Месяц")
                query_params["Год"] = year
                query_params["Месяц"] = month
            elif required:
                missing_required.append(dim_name)
            continue

        if filter_type == "range":
            if period.get("from"):
                conditions.append(f"{dim_name} >= &Начало")
                query_params["Начало"] = period["from"]
            if period.get("to"):
                conditions.append(f"{dim_name} <= &Конец")
                query_params["Конец"] = period["to"]
            if required and not period.get("from") and not period.get("to"):
                missing_required.append(dim_name)
            continue

        # filter_type == "="
        if value is not None:
            param_key = dim_name.replace(" ", "_")
            conditions.append(f"{dim_name} = &{param_key}")
            query_params[param_key] = value
        elif default is not None:
            param_key = dim_name.replace(" ", "_")
            conditions.append(f"{dim_name} = &{param_key}")
            query_params[param_key] = default
        elif required:
            missing_required.append(dim_name)

    if missing_required:
        return {
            "query": None,
            "missing_required": missing_required,
            "params": query_params,
        }

    # SELECT
    if group_by:
        select_fields = group_by + [f"СУММА({resource}) КАК Значение"]
    else:
        select_fields = [f"СУММА({resource}) КАК Значение"]
    select_clause = ",\n    ".join(select_fields)

    # WHERE
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

    return {"query": query, "params": query_params}
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_query_builder.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add api/query_builder.py tests/test_query_builder.py
git commit -m "feat: query_builder uses enriched metadata — required filters, ГОД/МЕСЯЦ, defaults"
```

---

### Task 5: Update param_extractor prompt with allowed values

**Files:**
- Modify: `prompts/param_extractor.txt`
- Modify: `api/param_extractor.py`
- Modify: `tests/test_param_extractor.py`

The LLM prompt now shows allowed values for each dimension, so the LLM picks from a list instead of guessing. Period format changes from `{from, to}` to `{year, month}`.

- [ ] **Step 1: Rewrite param_extractor.txt prompt**

```
Ты извлекаешь параметры запроса из вопроса пользователя.

Тебе даны метаданные регистра с допустимыми значениями для каждого измерения.
Выбирай значения ТОЛЬКО из списка допустимых. Не придумывай значения.

ПРАВИЛА:
1. Ответь ТОЛЬКО валидным JSON, без пояснений и markdown
2. Для каждого обязательного измерения ОБЯЗАТЕЛЬНО укажи значение из списка
3. Если значение не определено из вопроса, но есть default — используй default
4. Если значение не определено и нет default — поставь null и needs_clarification = true
5. resource — всегда "Сумма" (если в метаданных не указано иначе)
6. Период: извлеки год и месяц из текста (например "март 2025" → year: 2025, month: 3)
7. group_by — список измерений для группировки (пустой если не нужна)
8. needs_clarification = true, если не удалось определить обязательные параметры

{metadata}

ФОРМАТ ОТВЕТА:
{
  "resource": "Сумма",
  "filters": {
    "имя_измерения": "значение из списка допустимых или null"
  },
  "period": {
    "year": 2025,
    "month": 3
  },
  "group_by": [],
  "order_by": "desc",
  "limit": 1000,
  "needs_clarification": false,
  "understood": {
    "описание": "что я понял из вопроса — кратко, по-русски"
  }
}

Вопрос пользователя: {question}
```

- [ ] **Step 2: Update _format_metadata in param_extractor.py**

Show allowed values and required/default info:

```python
def _format_metadata(register_metadata: dict) -> str:
    """Format register metadata for LLM prompt with allowed values."""
    lines = [f"Регистр: {register_metadata['name']}"]
    if register_metadata.get("description"):
        lines.append(f"Описание: {register_metadata['description']}")

    lines.append("")
    lines.append("ИЗМЕРЕНИЯ:")
    for dim in register_metadata.get("dimensions", []):
        required = "ОБЯЗАТЕЛЬНОЕ" if dim.get("required") else "необязательное"
        default = dim.get("default_value") or dim.get("default")
        default_str = f", по умолчанию: \"{default}\"" if default else ""
        filter_type = dim.get("filter_type", "=")

        line = f"  {dim['name']} ({dim['data_type']}) — {required}{default_str}"

        if filter_type == "year_month":
            line += ", фильтр: ГОД() и МЕСЯЦ()"

        values = dim.get("allowed_values") or dim.get("values", [])
        if values:
            if len(values) <= 20:
                line += f"\n    Допустимые значения: {values}"
            else:
                line += f"\n    Допустимые значения ({len(values)}): {values[:20]}..."

        lines.append(line)

    lines.append("")
    lines.append("РЕСУРСЫ:")
    for res in register_metadata.get("resources", []):
        lines.append(f"  {res['name']} ({res.get('data_type', 'Число')})")

    return "\n".join(lines)
```

- [ ] **Step 3: Update _build_clarification to show allowed values**

```python
def _build_clarification(params: dict, register_metadata: dict) -> str:
    """Build a clarification question listing what we understood and what's missing."""
    lines = ["Правильно я поняла:"]

    understood = params.get("understood", {})
    desc = understood.get("описание", "")
    if desc:
        lines.append(f"- {desc}")

    filters = params.get("filters", {})
    for key, val in filters.items():
        if val is not None:
            lines.append(f"- {key}: {val}")

    period = params.get("period", {})
    if period.get("year") and period.get("month"):
        lines.append(f"- Период: {period['month']:02d}.{period['year']}")
    else:
        lines.append("- Период: не указан")

    # Show what's missing with allowed values
    dims_by_name = {d["name"]: d for d in register_metadata.get("dimensions", [])}
    missing = []
    for dim_name, dim in dims_by_name.items():
        if not dim.get("required"):
            continue
        if dim.get("filter_type") == "year_month":
            if not period.get("year") or not period.get("month"):
                missing.append(f"- {dim_name}: укажите год и месяц")
            continue
        value = filters.get(dim_name)
        default = dim.get("default_value") or dim.get("default")
        if value is None and default is None:
            values = dim.get("allowed_values") or dim.get("values", [])
            if values:
                missing.append(f"- {dim_name}: выберите из {values}")
            else:
                missing.append(f"- {dim_name}: ?")

    if missing:
        lines.append("")
        lines.append("Не хватает:")
        lines.extend(missing)

    lines.append("")
    lines.append("Уточните или подтвердите.")
    return "\n".join(lines)
```

- [ ] **Step 4: Remove _apply_date_fallback, update extract_params**

The date format is now `{year, month}` — no need for range-based fallback. Remove `_apply_date_fallback` and the imports of `date_parser`. The LLM extracts year/month directly.

In `extract_params`, remove the fallback block and simplify:

```python
async def extract_params(
    message: str,
    register_metadata: dict,
) -> dict:
    metadata_text = _format_metadata(register_metadata)
    prompt = _SYSTEM_PROMPT.replace("{metadata}", metadata_text).replace(
        "{question}", message
    )

    response = await generate(role="query", system_prompt=prompt, user_message=message)
    raw_response = response
    params = _parse_llm_json(response)

    if not params:
        return {
            "params": None,
            "needs_clarification": True,
            "clarification_text": "Не удалось разобрать ваш вопрос. Попробуйте переформулировать.",
            "debug": {
                "input_message": message,
                "metadata_sent": metadata_text,
                "raw_llm_response": raw_response,
                "parsed": None,
            },
        }

    needs_clarification = params.get("needs_clarification", False)
    clarification_text = None
    if needs_clarification:
        clarification_text = _build_clarification(params, register_metadata)

    return {
        "params": params,
        "needs_clarification": needs_clarification,
        "clarification_text": clarification_text,
        "debug": {
            "input_message": message,
            "metadata_sent": metadata_text,
            "raw_llm_response": raw_response,
            "parsed": params,
        },
    }
```

- [ ] **Step 5: Update tests**

Update `tests/test_param_extractor.py` fixtures to use enriched metadata and `{year, month}` period format in LLM mock responses.

- [ ] **Step 6: Run all tests**

```bash
python3 -m pytest tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add prompts/param_extractor.txt api/param_extractor.py tests/test_param_extractor.py
git commit -m "feat: param_extractor shows allowed values, period as year/month"
```

---

### Task 6: Handle missing_required in main.py

**Files:**
- Modify: `api/main.py`

When `build_query` returns `missing_required`, treat it like a clarification — tell user what's missing with allowed values.

- [ ] **Step 1: Update _execute_query_flow**

After calling `build_query`, check for `missing_required`:

```python
    result = build_query(params, register_meta)

    if result.get("missing_required"):
        # Build clarification from missing fields
        missing = result["missing_required"]
        dims_by_name = {d["name"]: d for d in register_meta.get("dimensions", [])}
        lines = ["Не хватает обязательных параметров:"]
        for name in missing:
            dim = dims_by_name.get(name, {})
            values = dim.get("allowed_values") or dim.get("values", [])
            if dim.get("filter_type") == "year_month":
                lines.append(f"- {name}: укажите год и месяц (например: март 2025)")
            elif values:
                lines.append(f"- {name}: выберите из {values}")
            else:
                lines.append(f"- {name}: укажите значение")

        debug["steps"].append({
            "step": "query_builder",
            "missing_required": missing,
        })

        # Store as pending clarification
        _pending_clarifications[session_id] = {
            "params": params,
            "register_metadata": register_meta,
        }

        return {
            "answer": "\n".join(lines),
            "register_name": register_name,
            "needs_clarification": True,
        }
```

Note: `session_id` needs to be passed to `_execute_query_flow`. Update its signature to accept `session_id`.

- [ ] **Step 2: Run all tests**

```bash
python3 -m pytest tests/ -v
```

- [ ] **Step 3: Commit**

```bash
git add api/main.py
git commit -m "feat: missing required filters trigger clarification with allowed values"
```

---

### Task 7: Update validator for enriched metadata

**Files:**
- Modify: `api/query_validator.py`

Add validation that param values are in the allowed list.

- [ ] **Step 1: Add validate_params function**

```python
def validate_params(params: dict, register_metadata: dict) -> tuple[bool, list[str]]:
    """Validate that param values are in allowed lists.

    Returns (is_valid, list of error messages).
    """
    errors = []
    filters = params.get("filters", {})
    dims_by_name = {d["name"]: d for d in register_metadata.get("dimensions", [])}

    for dim_name, value in filters.items():
        if value is None:
            continue
        dim = dims_by_name.get(dim_name)
        if not dim:
            continue
        allowed = dim.get("allowed_values") or dim.get("values", [])
        if allowed and value not in allowed:
            errors.append(f"{dim_name}: '{value}' не из допустимых {allowed}")

    return len(errors) == 0, errors
```

- [ ] **Step 2: Call validate_params in _execute_query_flow before build_query**

In `main.py`, after extracting params and before calling `build_query`:

```python
    from .query_validator import validate_params
    is_valid, val_errors = validate_params(params, register_meta)
    if not is_valid:
        debug["steps"].append({"step": "param_validation", "errors": val_errors})
        return {
            "answer": "Некорректные значения:\n" + "\n".join(f"- {e}" for e in val_errors),
            "register_name": register_name,
            "needs_clarification": True,
        }
```

- [ ] **Step 3: Run all tests**

```bash
python3 -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add api/query_validator.py api/main.py
git commit -m "feat: validate param values against allowed lists before query building"
```

---

### Task 8: Update e2e tests for new flow

**Files:**
- Modify: `tests/test_chat_e2e.py`

Update mock responses to use enriched metadata format and `{year, month}` period.

- [ ] **Step 1: Update test fixtures and mocks**

Update `_setup_dbs` to include enriched dimension columns in the test DB schema. Update mock LLM responses to return `{year, month}` period format. Update assertions.

- [ ] **Step 2: Run full test suite**

```bash
python3 -m pytest tests/ -v
```

Expected: all 60+ tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test: update e2e tests for enriched metadata and year/month period format"
```

---

### Task 9: Final integration test

- [ ] **Step 1: Seed DB from enriched YAML and start server**

```bash
python3 scripts/seed_metadata.py
uvicorn api.main:app --reload --port 8000
```

- [ ] **Step 2: Test in browser**

Open http://localhost:8000/web/ and ask: "какая выручка за март 2025"

Verify in debug panel:
1. Metadata: register found with enriched dimensions showing `required`, `values`
2. Param Extractor: LLM picks values from allowed lists
3. Query Builder: generates query with ГОД()/МЕСЯЦ() and all required filters
4. No missing filters

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: enriched metadata pipeline — required filters, allowed values, ГОД/МЕСЯЦ"
```
