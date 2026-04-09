"""Tests for param_extractor — LLM extracts structured JSON."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from api.param_extractor import extract_params


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
                "allowed_values": ["Газпром нефть", "СИБУР"],
            },
        ],
        "resources": [
            {"name": "Сумма", "data_type": "Число"},
        ],
    }


@pytest.mark.asyncio
async def test_full_extraction(register_meta):
    """LLM returns complete params with year/month period — no clarification needed."""
    llm_response = json.dumps({
        "resource": "Сумма",
        "filters": {
            "Сценарий": "Факт",
            "КонтурПоказателя": "свод",
            "Показатель": "Выручка",
            "ДЗО": "Газпром нефть",
        },
        "period": {"year": 2025, "month": 3},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
        "needs_clarification": False,
        "understood": {"описание": "Сумма выручки за март 2025"},
    })

    with patch("api.param_extractor.generate", new_callable=AsyncMock, return_value=llm_response):
        result = await extract_params("выручка за март 2025", register_meta)

    assert not result["needs_clarification"]
    assert result["params"]["resource"] == "Сумма"
    assert result["params"]["period"]["year"] == 2025
    assert result["params"]["period"]["month"] == 3


@pytest.mark.asyncio
async def test_clarification_needed(register_meta):
    """LLM can't determine required param — asks for clarification."""
    llm_response = json.dumps({
        "resource": "Сумма",
        "filters": {
            "Сценарий": "Факт",
            "КонтурПоказателя": "свод",
            "ДЗО": None,
        },
        "period": {"year": None, "month": None},
        "group_by": ["Показатель"],
        "order_by": "desc",
        "limit": 1000,
        "needs_clarification": True,
        "understood": {"описание": "Выручка по показателям, период и ДЗО не указаны"},
    })

    with patch("api.param_extractor.generate", new_callable=AsyncMock, return_value=llm_response):
        result = await extract_params("показатели по выручке", register_meta)

    assert result["needs_clarification"]
    assert "Правильно я поняла" in result["clarification_text"]
    assert "Период: не указан" in result["clarification_text"]
    # Shows missing required fields with allowed values
    assert "Не хватает" in result["clarification_text"]


@pytest.mark.asyncio
async def test_invalid_json_response(register_meta):
    """LLM returns garbage — graceful fallback."""
    with patch("api.param_extractor.generate", new_callable=AsyncMock, return_value="это не JSON"):
        result = await extract_params("выручка за март", register_meta)

    assert result["needs_clarification"]
    assert "переформулировать" in result["clarification_text"]
    assert result["params"] is None


@pytest.mark.asyncio
async def test_markdown_fenced_json(register_meta):
    """LLM wraps JSON in markdown code fences — still parses."""
    inner = json.dumps({
        "resource": "Сумма",
        "filters": {"Сценарий": "Факт", "Показатель": "Выручка", "ДЗО": "СИБУР"},
        "period": {"year": 2025, "month": 3},
        "group_by": [],
        "order_by": "desc",
        "limit": 1000,
        "needs_clarification": False,
        "understood": {"описание": "Сумма за март 2025"},
    })
    llm_response = f"```json\n{inner}\n```"

    with patch("api.param_extractor.generate", new_callable=AsyncMock, return_value=llm_response):
        result = await extract_params("выручка за март 2025", register_meta)

    assert not result["needs_clarification"]
    assert result["params"]["resource"] == "Сумма"
    assert result["params"]["period"]["year"] == 2025


@pytest.mark.asyncio
async def test_metadata_format_shows_allowed_values(register_meta):
    """_format_metadata includes allowed values and required/optional labels."""
    from api.param_extractor import _format_metadata

    text = _format_metadata(register_meta)

    assert "обязательное" in text
    assert "Факт, План, Прогноз" in text
    assert "по умолчанию: Факт" in text
    assert "ГОД/МЕСЯЦ" in text
