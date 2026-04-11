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


@pytest.fixture()
def register_meta_annotated():
    """Register metadata with technical/role/description_en annotations."""
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
                "name": "Масштаб",
                "data_type": "Строка",
                "required": False,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["тыс.", "млн."],
                "technical": True,
            },
            {
                "name": "Показатель_номер",
                "data_type": "Число",
                "required": False,
                "filter_type": "=",
                "technical": True,
            },
            {
                "name": "Период_Показателя",
                "data_type": "Дата",
                "required": True,
                "default_value": None,
                "filter_type": "year_month",
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
        ],
        "resources": [
            {"name": "Сумма", "data_type": "Число"},
        ],
    }


def test_technical_fields_excluded_from_filter_props(register_meta_annotated):
    """Fields with technical=True must not appear in filter properties."""
    tools = build_tools(register_meta_annotated)
    agg = next(t for t in tools if t["function"]["name"] == "aggregate")
    props = agg["function"]["parameters"]["properties"]
    assert "scale" not in props      # Масштаб is technical
    assert "metric_number" not in props  # Показатель_номер is technical
    assert "scenario" in props       # Сценарий is not technical
    assert "company" in props        # ДЗО is not technical


def test_role_filter_excluded_from_groupable(register_meta_annotated):
    """Fields with role=filter must not appear in group_by enum."""
    tools = build_tools(register_meta_annotated)
    gb = next(t for t in tools if t["function"]["name"] == "group_by")
    group_by_enum = gb["function"]["parameters"]["properties"]["group_by"]["enum"]
    assert "contour" not in group_by_enum  # КонтурПоказателя role=filter
    assert "company" in group_by_enum      # ДЗО role=both
    assert "scenario" in group_by_enum     # Сценарий role=both
    assert "metric" in group_by_enum       # Показатель role=both


def test_description_en_used_in_schema(register_meta_annotated):
    """description_en from metadata appears in tool schema property description."""
    tools = build_tools(register_meta_annotated)
    agg = next(t for t in tools if t["function"]["name"] == "aggregate")
    props = agg["function"]["parameters"]["properties"]
    assert "company / subsidiary" in props["company"]["description"]
