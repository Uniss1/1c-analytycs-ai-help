"""Tests for query_templates.try_match and date_parser."""

from unittest.mock import patch
from datetime import date

import pytest

from api.query_templates import try_match
from api.date_parser import parse_period


@pytest.fixture()
def register_meta():
    return {
        "name": "РегистрНакопления.ВитринаВыручка",
        "description": "Выручка по подразделениям и номенклатуре",
        "register_type": "accumulation_turnover",
        "dimensions": [
            {"name": "Подразделение", "data_type": "Справочник.Подразделения"},
        ],
        "resources": [
            {"name": "Сумма", "data_type": "Число"},
        ],
    }


# --- try_match tests ---


def test_sum_for_period(register_meta):
    result = try_match("выручка за март", register_meta)
    assert result is not None
    assert "СУММА(Сумма)" in result["query"]
    assert "ВитринаВыручка.Обороты" in result["query"]


def test_sum_by_dimension(register_meta):
    result = try_match("выручка по подразделениям", register_meta)
    assert result is not None
    assert "СГРУППИРОВАТЬ ПО Подразделение" in result["query"]
    assert "СУММА(Сумма)" in result["query"]


def test_top_n(register_meta):
    result = try_match("топ-5 по продажам", register_meta)
    assert result is not None
    assert "ПЕРВЫЕ 5" in result["query"]
    assert "УБЫВ" in result["query"]


def test_no_match(register_meta):
    result = try_match("сравни Q1 и Q2", register_meta)
    assert result is None


# --- date_parser tests ---


@patch("api.date_parser.date")
def test_date_parser_month(mock_date):
    mock_date.today.return_value = date(2025, 4, 9)
    mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
    result = parse_period("за март")
    assert result == {"Начало": "2025-03-01", "Конец": "2025-03-31"}


def test_date_parser_quarter():
    result = parse_period("за 1 квартал 2025")
    assert result == {"Начало": "2025-01-01", "Конец": "2025-03-31"}


def test_date_parser_year():
    result = parse_period("за 2024 год")
    assert result == {"Начало": "2024-01-01", "Конец": "2024-12-31"}
