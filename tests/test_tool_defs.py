# tests/test_tool_defs.py
"""Tests for tool_defs — single query tool schema generation."""

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
                "technical": False,
                "role": "both",
                "description_en": "scenario type (Факт, План, Прогноз)",
            },
            {
                "name": "КонтурПоказателя",
                "data_type": "Строка",
                "required": True,
                "default_value": "свод",
                "filter_type": "=",
                "allowed_values": ["свод", "детализация"],
                "technical": False,
                "role": "filter",
                "description_en": "data contour / aggregation level",
            },
            {
                "name": "Показатель",
                "data_type": "Строка",
                "required": True,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["Выручка", "ОЗП", "Маржа", "EBITDA"],
                "technical": False,
                "role": "both",
                "description_en": "metric name (Выручка, Маржа, EBITDA)",
            },
            {
                "name": "ДЗО",
                "data_type": "Строка",
                "required": True,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["Консолидация", "ДЗО-1", "ДЗО-2"],
                "technical": False,
                "role": "both",
                "description_en": "company / subsidiary (ДЗО, организация)",
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
                "technical": True,
            },
        ],
        "resources": [
            {"name": "Сумма", "data_type": "Число"},
        ],
    }


def test_build_tools_returns_one(register_meta):
    tools = build_tools(register_meta)
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "query"


def test_query_has_mode_enum(register_meta):
    tools = build_tools(register_meta)
    props = tools[0]["function"]["parameters"]["properties"]
    assert "mode" in props
    assert set(props["mode"]["enum"]) == {"aggregate", "group_by", "compare"}


def test_query_has_filter_dims(register_meta):
    tools = build_tools(register_meta)
    props = tools[0]["function"]["parameters"]["properties"]
    assert "scenario" in props
    assert "metric" in props
    assert "company" in props
    assert "contour" in props


def test_technical_excluded(register_meta):
    tools = build_tools(register_meta)
    props = tools[0]["function"]["parameters"]["properties"]
    assert "scale" not in props  # Масштаб is technical


def test_query_has_group_by_with_groupable_only(register_meta):
    tools = build_tools(register_meta)
    props = tools[0]["function"]["parameters"]["properties"]
    assert "group_by" in props
    group_enum = props["group_by"]["enum"]
    assert "contour" not in group_enum  # role=filter
    assert "company" in group_enum     # role=both
    assert "scenario" in group_enum    # role=both


def test_query_has_compare_fields(register_meta):
    tools = build_tools(register_meta)
    props = tools[0]["function"]["parameters"]["properties"]
    assert "compare_values" in props
    assert props["compare_values"]["type"] == "array"
    assert "compare_by" in props


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


def test_key_to_dim_roundtrip():
    assert key_to_dim("scenario") == "Сценарий"
    assert key_to_dim("company") == "ДЗО"
    assert key_to_dim("metric") == "Показатель"
    assert key_to_dim("unknown_key") == "unknown_key"


def test_system_message_has_few_shot(register_meta):
    msg = build_system_message(register_meta)
    assert "query(" in msg  # few-shot examples use query() format
    assert "aggregate" in msg
    assert "group_by" in msg
    assert "compare" in msg
    assert "ALWAYS call" in msg
    assert "Витрина_Дашборда" in msg


def test_system_message_examples_use_register_enum_values(register_meta):
    """Few-shot examples must pull values from the register's allowed_values,
    not from a hardcoded set that might not exist in the actual register."""
    msg = build_system_message(register_meta)
    # Values from this register's enums should appear in examples
    assert "Выручка" in msg  # metric's first allowed
    # compare_values should use first two scenario values
    assert '"Факт"' in msg and '"Прогноз"' in msg


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


def test_system_message_adapts_to_different_register():
    """Swapping register with different enum values changes the examples."""
    meta = {
        "name": "РегистрСведений.АнотерРегистр",
        "dimensions": [
            {
                "name": "Показатель",
                "filter_type": "=",
                "role": "filter",
                "allowed_values": ["Выручка от реализации", "Прочее"],
                "required": True,
            },
            {
                "name": "ДЗО",
                "filter_type": "=",
                "role": "both",
                "allowed_values": ["Альфа", "Бета"],
                "required": True,
            },
        ],
        "resources": [{"name": "Сумма_нетто"}],
    }
    msg = build_system_message(meta)
    # Example values must be from THIS register, not defaults
    assert "Выручка от реализации" in msg
    assert "Сумма_нетто" in msg
    assert "Альфа" in msg and "Бета" in msg
    # Old hardcoded values should NOT leak in as examples
    assert "EBITDA" not in msg or "Q: " not in msg.split("EBITDA")[0][-50:]


def test_system_message_has_array_filter_example(register_meta):
    msg = build_system_message(register_meta)
    # At least one example should pass a filter as an array literal
    assert '["' in msg, "Expected array-literal filter in few-shot"


def test_system_message_has_year_only_example(register_meta):
    """A 'whole year' example must appear — year without month."""
    msg = build_system_message(register_meta)
    assert "за 2024 год" in msg or "за 2025 год" in msg
    # The answer line following a year-only Q must have year= but not month=
    lines = msg.splitlines()
    year_only_q = next(
        (i for i, l in enumerate(lines)
         if "год" in l and l.lstrip().startswith("Q:")
         and "март" not in l and "мая" not in l),
        None,
    )
    assert year_only_q is not None, "No year-only Q: line found"
    answer = lines[year_only_q + 1]
    assert "year=" in answer
    assert "month=" not in answer
