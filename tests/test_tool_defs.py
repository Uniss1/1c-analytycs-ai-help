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
    assert "month" in required
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
