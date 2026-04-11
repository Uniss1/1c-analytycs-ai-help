# tests/test_param_validator.py
"""Tests for param_validator — mode-based validation."""

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


def test_valid_tools_include_three_modes(register_meta):
    """aggregate, group_by, compare are all valid."""
    for tool in ("aggregate", "group_by", "compare"):
        result = validate(
            {"tool": tool, "params": {"resource": "Сумма", "filters": {}, "period": {"year": 2025, "month": 3}}},
            register_meta,
        )
        # May have other errors but tool name should be valid
        assert not any("инструмент" in e.lower() for e in result.errors)


def test_invalid_tool(register_meta):
    result = validate({"tool": "ratio", "params": {"resource": "Сумма"}}, register_meta)
    assert result.ok is False
    assert any("инструмент" in e.lower() or "tool" in e.lower() for e in result.errors)


def test_invalid_resource(register_meta):
    result = validate(
        {"tool": "aggregate", "params": {"resource": "Несуществующий", "filters": {}, "period": {"year": 2025, "month": 3}}},
        register_meta,
    )
    assert result.ok is False


def test_invalid_filter_value(register_meta):
    result = validate(
        {"tool": "aggregate", "params": {"resource": "Сумма", "filters": {"Сценарий": "XXX"}, "period": {"year": 2025, "month": 3}}},
        register_meta,
    )
    assert result.ok is False
    assert any("Сценарий" in e for e in result.errors)


def test_compare_needs_two_values(register_meta):
    result = validate(
        {"tool": "compare", "params": {"resource": "Сумма", "values": ["Факт"], "filters": {}, "period": {"year": 2025, "month": 3}}},
        register_meta,
    )
    assert result.ok is False


def test_compare_valid(register_meta):
    result = validate(
        {"tool": "compare", "params": {"resource": "Сумма", "compare_by": "Сценарий", "values": ["Факт", "План"], "filters": {}, "period": {"year": 2025, "month": 3}}},
        register_meta,
    )
    assert result.ok is True


def test_group_by_missing(register_meta):
    """group_by mode without group_by param should fail."""
    result = validate(
        {"tool": "group_by", "params": {"resource": "Сумма", "group_by": [], "filters": {}, "period": {"year": 2025, "month": 3}}},
        register_meta,
    )
    assert result.ok is False


def test_no_tool():
    result = validate({"tool": None, "error": "no tool call"}, {})
    assert result.ok is False
