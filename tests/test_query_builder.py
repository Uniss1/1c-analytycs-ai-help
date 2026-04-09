"""Tests for query_builder — deterministic JSON params → 1C query."""

import pytest

from api.query_builder import build_query


@pytest.fixture()
def register_meta():
    """Enriched register metadata matching real 1C structure."""
    return {
        "name": "РегистрСведений.Витрина_Дашборда",
        "description": "Витрина дашборда",
        "register_type": "information",
        "dimensions": [
            {
                "name": "Период_Показателя",
                "data_type": "Дата",
                "required": True,
                "default_value": None,
                "filter_type": "year_month",
                "allowed_values": [],
            },
            {
                "name": "Сценарий",
                "data_type": "Строка",
                "required": True,
                "default_value": "Факт",
                "filter_type": "=",
                "allowed_values": ["Факт", "План", "Прогноз"],
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
                "allowed_values": ["Выручка", "EBITDA", "Чистая прибыль"],
            },
            {
                "name": "ДЗО",
                "data_type": "Строка",
                "required": True,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["Газпром нефть", "СИБУР", "Роснефть"],
            },
            {
                "name": "Масштаб",
                "data_type": "Строка",
                "required": False,
                "default_value": None,
                "filter_type": "=",
                "allowed_values": ["тыс.", "млн.", "млрд."],
            },
        ],
        "resources": [
            {"name": "Сумма", "data_type": "Число"},
        ],
    }


def test_all_required_with_defaults(register_meta):
    """All required filters present — defaults fill in Сценарий and КонтурПоказателя."""
    params = {
        "resource": "Сумма",
        "filters": {
            "Показатель": "Выручка",
            "ДЗО": "Газпром нефть",
        },
        "period": {"year": 2025, "month": 3},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert result["query"] is not None
    assert result["missing_required"] == []
    # year_month generates ГОД()/МЕСЯЦ()
    assert "ГОД(Период_Показателя) = &Год" in result["query"]
    assert "МЕСЯЦ(Период_Показателя) = &Месяц" in result["query"]
    assert result["params"]["Год"] == 2025
    assert result["params"]["Месяц"] == 3
    # Defaults applied
    assert result["params"]["Сценарий"] == "Факт"
    assert result["params"]["КонтурПоказателя"] == "свод"
    # User-provided
    assert result["params"]["Показатель"] == "Выручка"
    assert result["params"]["ДЗО"] == "Газпром нефть"


def test_year_month_filter(register_meta):
    """year_month filter generates ГОД()/МЕСЯЦ() syntax."""
    params = {
        "resource": "Сумма",
        "filters": {
            "Показатель": "EBITDA",
            "ДЗО": "СИБУР",
        },
        "period": {"year": 2024, "month": 12},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert "ГОД(Период_Показателя) = &Год" in result["query"]
    assert "МЕСЯЦ(Период_Показателя) = &Месяц" in result["query"]
    # Must NOT contain >= / <= style
    assert ">=" not in result["query"]
    assert "<=" not in result["query"]


def test_group_by_excluded_from_where(register_meta):
    """Dimension in group_by goes to SELECT, not WHERE."""
    params = {
        "resource": "Сумма",
        "filters": {
            "Показатель": "Выручка",
        },
        "period": {"year": 2025, "month": 3},
        "group_by": ["ДЗО"],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert result["query"] is not None
    # ДЗО should be in SELECT and GROUP BY, not in WHERE as a filter
    assert "ДЗО," in result["query"]
    assert "СГРУППИРОВАТЬ ПО ДЗО" in result["query"]
    # ДЗО should NOT appear as a WHERE condition
    assert "ДЗО = &" not in result["query"]


def test_optional_filter_not_forced(register_meta):
    """Optional dimension (Масштаб) is skipped when not provided."""
    params = {
        "resource": "Сумма",
        "filters": {
            "Показатель": "Выручка",
            "ДЗО": "Роснефть",
        },
        "period": {"year": 2025, "month": 1},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert result["query"] is not None
    assert "Масштаб" not in result["query"]


def test_missing_required_returns_error(register_meta):
    """Missing required dimension (no value, no default) → error."""
    params = {
        "resource": "Сумма",
        "filters": {
            # Missing Показатель and ДЗО (required, no defaults)
        },
        "period": {"year": 2025, "month": 3},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert result["query"] is None
    assert "Показатель" in result["missing_required"]
    assert "ДЗО" in result["missing_required"]
    assert result["params"] == {}


def test_user_value_overrides_default(register_meta):
    """User-provided value overrides default_value."""
    params = {
        "resource": "Сумма",
        "filters": {
            "Сценарий": "План",  # overrides default "Факт"
            "КонтурПоказателя": "детализация",  # overrides default "свод"
            "Показатель": "Выручка",
            "ДЗО": "Роснефть",
        },
        "period": {"year": 2025, "month": 6},
        "group_by": [],
        "order_by": "asc",
        "limit": 5,
    }
    result = build_query(params, register_meta)

    assert result["query"] is not None
    assert result["params"]["Сценарий"] == "План"
    assert result["params"]["КонтурПоказателя"] == "детализация"
    assert "ПЕРВЫЕ 5" in result["query"]
    assert "ВОЗР" in result["query"]


def test_missing_period_when_required(register_meta):
    """Missing period when year_month dimension is required → error."""
    params = {
        "resource": "Сумма",
        "filters": {
            "Показатель": "Выручка",
            "ДЗО": "Газпром нефть",
        },
        "period": {},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
    }
    result = build_query(params, register_meta)

    assert result["query"] is None
    assert "Период_Показателя" in result["missing_required"]
